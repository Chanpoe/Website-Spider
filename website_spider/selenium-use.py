import json
import time
import os
import random
import threading
import multiprocessing as mp
import logging
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import List, Dict, Tuple, Union
from contextlib import contextmanager

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from loguru import logger

# 降低 urllib3 对连接重试的噪声日志级别，避免在 driver 关闭时刷 Warning
try:
    logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
except Exception:
    pass


class MultitabWebSpider:
    """单浏览器多标签页并发访问网站的爬虫类"""

    def __init__(self, headless: bool = True, timeout: int = 30):
        """
        初始化爬虫

        :param headless: 是否使用无头模式
        :param timeout: 页面加载超时时间
        """
        self.headless = headless
        self.timeout = timeout
        self.driver = None
        self._lock = threading.Lock()

    def _create_driver(self) -> uc.Chrome:
        """创建Chrome WebDriver实例"""

        # 进程间文件锁，避免 undetected_chromedriver 并发下载/改名驱动时产生竞态
        def _acquire_uc_lock() -> int:
            try:
                lock_path = '/tmp/uc_chromedriver.lock'
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
                try:
                    import fcntl  # type: ignore
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except Exception:
                    # 非 *nix 或锁失败时忽略，降级无锁
                    pass
                return fd
            except Exception:
                return -1

        def _release_uc_lock(fd: int) -> None:
            if fd and fd > 0:
                try:
                    try:
                        import fcntl  # type: ignore
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                    os.close(fd)
                except Exception:
                    pass

        options = uc.ChromeOptions()

        if self.headless:
            # 使用旧版 headless，并配合必要的稳定参数（与之前验证通过策略一致）
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

        # 更“保险”的通用参数集合（与之前通过的策略对齐）
        common_args = [
            "--disable-web-security",
            "--allow-running-insecure-content",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
            "--disable-default-apps",
            "--disable-sync",
            "--disable-translate",
            "--hide-scrollbars",
            "--mute-audio",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
            "--disable-client-side-phishing-detection",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-features=VizDisplayCompositor",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-background-networking",
            "--disable-background-downloads",
            "--disable-background-upload",
            "--window-size=1920,1080",
            "--start-maximized",
        ]
        for arg in common_args:
            options.add_argument(arg)

        # （保留语言设置无伤大雅）
        try:
            options.add_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
            options.add_experimental_option("prefs", {"intl.accept_languages": "zh-CN,zh"})
        except Exception:
            pass

        # 降低自动化可观测性（此环境对 excludeSwitches/useAutomationExtension 不兼容，跳过）

        # 不使用持久化用户目录（保持与之前策略一致）

        # 设置页面加载策略为 eager（也可用 normal，按需）
        options.page_load_strategy = 'eager'

        # 启用 performance 日志，便于从网络事件中提取状态码（避免使用 JS fetch 触发风控）
        try:
            options.add_argument("--enable-logging")
            options.add_argument("--log-level=0")
            options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        except Exception:
            pass

        # 解决 UC 下载驱动时在部分 macOS 环境下的证书校验错误
        try:
            import ssl  # noqa: WPS433
            ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]
        except Exception:
            pass

        # 可选：接受不安全证书（保持默认，不强行声明）

        # 如果主进程已预热并提供 driver 路径，则跳过锁并直接使用已有驱动
        prewarmed = os.environ.get('UC_PREWARMED') == '1'
        preset_driver_path = os.environ.get('UC_DRIVER_EXECUTABLE') or None
        if preset_driver_path and os.path.exists(preset_driver_path):
            driver = uc.Chrome(
                options=options,
                use_subprocess=True,
                driver_executable_path=preset_driver_path,
                enable_cdp_events=True,
            )
        else:
            lock_fd = _acquire_uc_lock() if not prewarmed else -1
            try:
                driver = uc.Chrome(
                    options=options,
                    use_subprocess=True,
                    driver_executable_path=None,
                    enable_cdp_events=True,
                )
            finally:
                _release_uc_lock(lock_fd)

        # 尝试开启 Network 域，确保可以接收到 response 事件
        try:
            driver.execute_cdp_cmd('Network.enable', {})
        except Exception:
            pass

        # 注入额外的 navigator 伪装，贴合之前通过的策略
        try:
            driver.execute_script(
                """
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: () => ({
                        length: 5,
                        0: { type: 'application/pdf', description: 'Portable Document Format' },
                        1: { type: 'application/x-google-chrome-pdf', description: 'Portable Document Format' },
                        2: { type: 'application/x-nacl', description: 'Native Client Executable' },
                        3: { type: 'application/x-shockwave-flash', description: 'Shockwave Flash' },
                        4: { type: 'application/futuresplash', description: 'FutureSplash Player' }
                    })
                });
                """
            )
        except Exception:
            pass

        # 不强行开启 CDP 网络域

        return driver

    def _try_bypass_ssl_interstitial(self, fast: bool = False) -> None:
        """尝试绕过 Chrome SSL 隐私拦截页。
        fast=True 使用无等待的快速探测，避免阻塞事件循环。
        """

        def _click_if(selector: str) -> bool:
            try:
                elems = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elems:
                    try:
                        elems[0].click()
                        return True
                    except Exception:
                        pass
            except Exception:
                pass
            return False

        try:
            if fast:
                # 快速模式：无等待直接探测点击
                # 1) 展开“高级”
                if _click_if('#details-button') or _click_if('#advancedButton'):
                    time.sleep(0.1)
                # 2) 继续前往（忽略风险）
                if _click_if('#proceed-link') or _click_if('button#primary-button'):
                    return
                # 3) 兜底：输入 thisisunsafe
                try:
                    ActionChains(self.driver).send_keys('thisisunsafe').perform()
                except Exception:
                    pass
                return
            else:
                # 正常模式：带短等待
                try:
                    btn = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, '#details-button, #advancedButton'))
                    )
                    btn.click()
                except Exception:
                    pass
                try:
                    go = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, '#proceed-link, button#primary-button'))
                    )
                    go.click()
                    return
                except Exception:
                    pass
                try:
                    ActionChains(self.driver).send_keys('thisisunsafe').perform()
                except Exception:
                    pass
        except Exception:
            pass

    def __enter__(self):
        """进入上下文管理器"""
        self.driver = self._create_driver()
        try:
            # 限制脚本执行时间，避免单次 execute_script 长时间阻塞事件循环
            self.driver.set_script_timeout(3)
            # 页加载超时（对 driver.get 生效，对 JS 导航不一定生效，但保留）
            self.driver.set_page_load_timeout(self.timeout)
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("浏览器已关闭")
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")

    def _get_status_code(self, url: str) -> int:
        """从 performance 日志获取状态码，避免使用 JS fetch 触发风控。"""
        # 优先：从 performance 日志读取 Network.responseReceived
        try:
            service = getattr(self.driver, 'service', None)
            is_alive = True
            try:
                if service and hasattr(service, 'is_connectable'):
                    is_alive = bool(service.is_connectable())
            except Exception:
                is_alive = True
            if is_alive:
                logs = self.driver.get_log('performance')
                for entry in logs:
                    try:
                        msg = json.loads(entry['message'])
                        m = msg.get('message', {})
                        if m.get('method') == 'Network.responseReceived':
                            resp = m.get('params', {}).get('response', {})
                            if resp.get('url') == url:
                                status = int(resp.get('status') or 0)
                                if status:
                                    return status
                    except Exception:
                        continue
        except Exception:
            pass

        # 兜底：简单就绪判断推断 200（不可靠，仅兜底）
        try:
            if self.driver.find_elements(By.TAG_NAME, 'body') and len(self.driver.page_source) > 100:
                return 200
        except Exception:
            pass
        return 0

    def _get_page_content(self, url: str, tab_handle: str) -> Dict:
        """在指定标签页获取页面内容"""
        try:
            with self._lock:
                self.driver.switch_to.window(tab_handle)

            # 设置超时并访问页面
            self.driver.set_page_load_timeout(self.timeout)
            self.driver.get(url)
            # 尝试绕过隐私拦截页
            self._try_bypass_ssl_interstitial(fast=False)

            # 等待页面基本加载完成
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except Exception:
                logger.warning(f"等待页面加载超时: {url}")

            # 获取页面源码
            source_code = self.driver.page_source
            status_code = self._get_status_code(url)

            return {
                'url': url,
                'source_code': source_code,
                'status': 'success',
                'status_code': status_code,
                'content_length': len(source_code)
            }

        except Exception as e:
            logger.error(f"获取页面内容失败 {url}: {e}")
            return {
                'url': url,
                'source_code': '',
                'status': 'failed',
                'status_code': 0,
                'error': str(e),
                'content_length': 0
            }

    def crawl_urls(self, urls: List[str], max_tabs: int = 5) -> List[Dict]:
        """
        单线程事件循环的“动态标签池”并发：
        - 同时保持最多 max_tabs 个标签页在加载
        - 谁先就绪就先采集并立刻分配下一个 URL
        - 避免多线程争用同一 WebDriver 的不稳定性
        """
        if not urls:
            return []

        max_tabs = max(1, max_tabs)
        total = len(urls)
        url_iter = iter(enumerate(urls))
        results_by_index: Dict[int, Dict] = {}

        # 打开初始标签页集合（直接用 window.open(url)）
        active_tabs: Dict[str, Dict] = {}  # handle -> {idx, url, start, bypassed}
        base_handle = self.driver.current_window_handle
        created = 0
        while created < min(max_tabs, total):
            try:
                idx, u = next(url_iter)
            except StopIteration:
                break
            try:
                self.driver.execute_script("window.open(arguments[0], '_blank');", u)
                new_handle = self.driver.window_handles[-1]
                active_tabs[new_handle] = {'idx': idx, 'url': u, 'start': time.time(), 'bypassed': False}
                created += 1
            except Exception as e:
                logger.error(f"创建初始标签失败: {e}")
                # 记录失败并尝试继续下一条
                results_by_index[idx] = {
                    'url': u,
                    'source_code': '',
                    'status': 'failed',
                    'error': str(e),
                    'content_length': 0
                }

        # 事件循环：轮询所有活动标签，判定是否就绪或超时
        poll_interval = 0.2
        hard_kill_after = max(self.timeout * 2, self.timeout + 5)
        while active_tabs:
            for handle in list(active_tabs.keys()):
                info = active_tabs.get(handle)
                if not info:
                    continue
                idx = info['idx']
                u = info['url']
                start = info['start']
                elapsed = time.time() - start
                ready = False
                try:
                    self.driver.switch_to.window(handle)
                    # 若尚未尝试过绕过隐私拦截，先进行一次快速尝试
                    if not info.get('bypassed'):
                        self._try_bypass_ssl_interstitial(fast=True)
                        info['bypassed'] = True
                    # 就绪判定：优先用 find_elements (更不易阻塞)
                    try:
                        has_body = bool(self.driver.find_elements(By.TAG_NAME, 'body'))
                    except Exception:
                        has_body = False
                    # 次要就绪信号：readyState
                    try:
                        rs = self.driver.execute_script('return document.readyState || "";') or ""
                    except Exception:
                        rs = ""
                    ready = has_body or (rs in ("interactive", "complete"))
                except Exception as e:
                    # 句柄异常（可能被网站自身关闭），视为失败
                    logger.error(f"检测标签状态失败 {u}: {e}")
                    results_by_index[idx] = {
                        'url': u,
                        'source_code': '',
                        'status': 'failed',
                        'error': str(e),
                        'content_length': 0
                    }
                    # 回收并尝试补位
                    try:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                    except Exception:
                        pass
                    active_tabs.pop(handle, None)
                    # 打开下一个 URL 的新标签
                    try:
                        idx2, u2 = next(url_iter)
                        self.driver.switch_to.window(base_handle)
                        self.driver.execute_script("window.open(arguments[0], '_blank');", u2)
                        new_handle = self.driver.window_handles[-1]
                        active_tabs[new_handle] = {'idx': idx2, 'url': u2, 'start': time.time(), 'bypassed': False}
                    except StopIteration:
                        pass
                    except Exception as e2:
                        logger.error(f"补位标签创建失败: {e2}")
                    continue

                # 若超出硬杀阈值，直接关闭并标记失败，避免无限阻塞
                if elapsed >= hard_kill_after:
                    logger.warning(f"标签超时强制关闭: {u} (elapsed={elapsed:.1f}s)")
                    try:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                    except Exception:
                        pass
                    active_tabs.pop(handle, None)
                    results_by_index[idx] = {
                        'url': u,
                        'source_code': '',
                        'status': 'failed',
                        'error': f'timeout>{hard_kill_after}s',
                        'content_length': 0
                    }
                    # 补位
                    try:
                        idx2, u2 = next(url_iter)
                        self.driver.switch_to.window(base_handle)
                        self.driver.execute_script("window.open(arguments[0], '_blank');", u2)
                        new_handle = self.driver.window_handles[-1]
                        active_tabs[new_handle] = {'idx': idx2, 'url': u2, 'start': time.time()}
                    except StopIteration:
                        pass
                    except Exception as e2:
                        logger.error(f"补位标签创建失败: {e2}")
                    continue

                if ready or elapsed >= self.timeout:
                    # 采集并回收/复用标签
                    try:
                        self.driver.switch_to.window(handle)
                        # 达到超时阈值时，尽量先停止加载，避免采集阻塞
                        if not ready:
                            try:
                                self.driver.execute_cdp_cmd('Page.stopLoading', {})
                            except Exception:
                                pass
                            try:
                                self.driver.execute_script('window.stop && window.stop();')
                            except Exception:
                                pass
                        source_code = self.driver.page_source
                        status_code = self._get_status_code(u)
                        results_by_index[idx] = {
                            'url': u,
                            'source_code': source_code,
                            'status': 'success',
                            'status_code': status_code,
                            'content_length': len(source_code)
                        }
                    except Exception as e:
                        logger.error(f"采集失败 {u}: {e}")
                        results_by_index[idx] = {
                            'url': u,
                            'source_code': '',
                            'status': 'failed',
                            'status_code': 0,
                            'error': str(e),
                            'content_length': 0
                        }
                    # 关闭已完成标签，并补位下一个 URL
                    try:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                    except Exception:
                        pass
                    active_tabs.pop(handle, None)
                    try:
                        idx2, u2 = next(url_iter)
                        self.driver.switch_to.window(base_handle)
                        self.driver.execute_script("window.open(arguments[0], '_blank');", u2)
                        new_handle = self.driver.window_handles[-1]
                        active_tabs[new_handle] = {'idx': idx2, 'url': u2, 'start': time.time(), 'bypassed': False}
                    except StopIteration:
                        pass
                    except Exception as e2:
                        logger.error(f"补位标签创建失败: {e2}")

            time.sleep(poll_interval)

        # 回到基础窗口
        try:
            if base_handle in self.driver.window_handles:
                self.driver.switch_to.window(base_handle)
        except Exception:
            pass

        # 返回按输入顺序的结果
        ordered = []
        for i, u in enumerate(urls):
            ordered.append(results_by_index.get(i, {
                'url': u,
                'source_code': '',
                'status': 'failed',
                'status_code': 0,
                'error': 'No result',
                'content_length': 0
            }))
        return ordered

    # 旧的分批实现已被动态池替换

    def _close_batch_tabs(self, tab_handles: List[str]):
        """关闭批次中的所有标签页"""
        for handle in tab_handles:
            if handle:
                try:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
                except Exception as e:
                    logger.debug(f"关闭标签页失败: {e}")

        # 切换回第一个标签页
        try:
            if self.driver.window_handles:
                self.driver.switch_to.window(self.driver.window_handles[0])
        except Exception as e:
            logger.debug(f"切换到主标签页失败: {e}")


def _fetch_single_url_with_fresh_driver(url: str, headless: bool = True, timeout: int = 30) -> Dict:
    """
    在独立浏览器会话中抓取单个 URL。
    注意：该函数会在内部创建并销毁一个浏览器实例，适合在线程/进程池中调用。
    """
    try:
        logger.info(f"PID={os.getpid()} 启动浏览器抓取 -> {url}")
        with MultitabWebSpider(headless=headless, timeout=timeout) as spider:
            base_handle = spider.driver.current_window_handle
            result = spider._get_page_content(url, base_handle)
            logger.info(f"PID={os.getpid()} 完成抓取 <- {url} len={result.get('content_length', 0)}")
            return result
    except Exception as e:
        logger.error(f"进程/线程抓取失败 {url}: {e}")
        return {
            'url': url,
            'source_code': '',
            'status': 'failed',
            'status_code': 0,
            'error': str(e),
            'content_length': 0
        }


def get_html_sources(urls: Union[str, List[str]],
                     headless: bool = True,
                     num_workers: int = 4,
                     timeout: int = 30,
                     save_to_file: str = None) -> Union[Dict, List[Dict]]:
    """
    多进程并发版：每个进程独立启动一个浏览器抓取，隔离性最佳。
    注意：macOS 默认使用 spawn，需要确保在 __main__ 保护下调用。
    """
    # 预热：在主进程中先启动并退出一次 UC，避免子进程首次并发下载驱动时出现竞态
    try:
        # 预热并记录实际使用到的 chromedriver 路径（如果 UC 暴露该信息的话）
        with MultitabWebSpider(headless=False, timeout=10) as warmup:
            try:
                # 尝试从 UC 提取 driver 可执行路径（不同版本实现可能不同）
                cd_path = getattr(getattr(warmup.driver, 'service', None), 'path', None)
                if cd_path and os.path.exists(cd_path):
                    os.environ['UC_DRIVER_EXECUTABLE'] = cd_path
                    os.environ['UC_PREWARMED'] = '1'
                else:
                    os.environ['UC_PREWARMED'] = '1'
            except Exception:
                os.environ['UC_PREWARMED'] = '1'
    except Exception as e:
        logger.warning(f"驱动预热失败（忽略）：{e}")

    is_single_url = isinstance(urls, str)
    url_list = [urls] if is_single_url else list(urls or [])
    if not url_list:
        return {} if is_single_url else []

    results_by_index: Dict[int, Dict] = {}
    # 使用进程池；每个任务独立一个浏览器实例
    with ProcessPoolExecutor(max_workers=max(1, num_workers), mp_context=mp.get_context('spawn')) as executor:
        future_to_index = {
            executor.submit(_fetch_single_url_with_fresh_driver, u, headless, timeout): i
            for i, u in enumerate(url_list)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results_by_index[idx] = future.result()
            except Exception as e:
                u = url_list[idx]
                results_by_index[idx] = {
                    'url': u,
                    'source_code': '',
                    'status': 'failed',
                    'status_code': 0,
                    'error': str(e),
                    'content_length': 0
                }

    ordered_results = [results_by_index[i] for i in range(len(url_list))]

    if save_to_file and ordered_results:
        with open(save_to_file, 'w', encoding='utf-8') as f:
            for result in ordered_results:
                json.dump(result, f, ensure_ascii=False)
                f.write('\n')
        logger.info(f"结果已保存到: {save_to_file}")

    return ordered_results[0] if is_single_url else ordered_results


if __name__ == '__main__':
    # 明确指定 spawn，避免 macOS 上的隐式行为差异
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass
    # 单站验证：chinattl
    # test_url = "http://www.chinattl.com/"
    # print("开始单站验证: ", test_url)

    test_url = "http://www.chinattl.com/"
    print("开始单站验证: ", test_url)

    # 使用简化接口但限制为单标签（等效单站）
    results = get_html_sources(
        [test_url],
        headless=False,
        num_workers=1,
        timeout=30,
        save_to_file=None,
    )

    # r = results[0] if results else {}
    # content_len = r.get('content_length', 0)
    # status = r.get('status')
    # print(f"结果: 状态={status}, 内容长度={content_len}")
    # if status != 'success':
    #     print("失败详情:", r)
    # exit()

    # # 读取URL列表
    # try:
    #     with open('urls.txt', 'r', encoding='utf-8') as f:
    #         url_list = [line.strip() for line in f if line.strip()]
    # except FileNotFoundError:
    #     # 如果没有urls.txt文件，使用示例URL
    #     url_list = [
    #         "https://www.baidu.com",
    #         "https://www.github.com",
    #         "https://www.google.com",
    #         "https://www.stackoverflow.com"
    #     ]
    #     logger.info("未找到urls.txt文件，使用示例URL进行测试")
    #
    # print(f"开始爬取 {len(url_list)} 个URL...")
    #
    # # 使用简化接口进行批量爬取
    # results = get_html_sources(
    #     url_list,
    #     headless=False,  # 使用有头模式便于观察
    #     max_tabs=5,  # 最多同时5个标签页
    #     timeout=30,  # 30秒超时
    #     save_to_file='crawl_results.jsonl'
    # )
    #
    # # 输出统计信息
    # print(f"\n爬取完成！共处理 {len(results)} 个URL")
    # success_count = len([r for r in results if r['status'] == 'success'])
    # print(f"成功: {success_count}, 失败: {len(results) - success_count}")
    #
    # # 显示详细结果
    # for result in results:
    #     url = result['url']
    #     status = result['status']
    #     length = result['content_length']
    #     print(f"URL: {url}")
    #     print(f"  状态: {status}, 内容长度: {length}")
    #     if status == 'failed' and 'error' in result:
    #         print(f"  错误: {result['error']}")
    #     print()
