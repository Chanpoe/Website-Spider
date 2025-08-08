import json
import time
import os
import random
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Union
from contextlib import contextmanager

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from loguru import logger


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
        options = uc.ChromeOptions()
        
        if self.headless:
            # 使用新版 headless，指纹更自然
            options.add_argument("--headless=new")
        
        # 自然化、尽量最少参数
        basic_args = [
            "--window-size=1920,1080",
        ]
        for arg in basic_args:
            options.add_argument(arg)
        
        # 语言与区域，贴近真实用户
        options.add_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
        try:
            options.add_experimental_option("prefs", {"intl.accept_languages": "zh-CN,zh"})
        except Exception:
            pass

        # 降低自动化可观测性（此环境对 excludeSwitches/useAutomationExtension 不兼容，跳过）

        # 使用持久化用户目录，减少“新装浏览器”指纹
        try:
            profile_dir = os.path.join(os.path.expanduser("~"), ".uc_chrome_profile")
            options.add_argument(f"--user-data-dir={profile_dir}")
        except Exception:
            pass

        # 设置页面加载策略为 eager（也可用 normal，按需）
        options.page_load_strategy = 'eager'

        # 不主动开启 performance 日志，减少可疑信号；状态码改用 JS fetch 兜底

        # 解决 UC 下载驱动时在部分 macOS 环境下的证书校验错误
        try:
            import ssl  # noqa: WPS433
            ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]
        except Exception:
            pass

        # 可选：接受不安全证书（保持默认，不强行声明）

        driver = uc.Chrome(options=options, use_subprocess=True, driver_executable_path=None, enable_cdp_events=False)

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
        """尽量从 performance 日志或 JS fetch 获取状态码。"""
        # 方式1: 从 performance 日志读取 Network.responseReceived
        try:
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

        # 方式2: 使用 JS fetch 兜底
        try:
            status_code = self.driver.execute_script(
                "return new Promise((resolve)=>{fetch(arguments[0]).then(r=>resolve(r.status)).catch(()=>resolve(0));});",
                url,
            )
            if status_code and int(status_code) != 0:
                return int(status_code)
        except Exception:
            pass

        # 方式3: 简单就绪判断推断 200（不可靠，仅兜底）
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


def get_html_sources(urls: Union[str, List[str]], 
                    headless: bool = True, 
                    max_tabs: int = 5,
                    timeout: int = 30,
                    save_to_file: str = None) -> Union[Dict, List[Dict]]:
    """
    获取一个或多个URL的HTML源码（简化版接口）
    
    :param urls: 单个URL字符串或URL列表
    :param headless: 是否使用无头模式
    :param max_tabs: 最大并发标签页数量
    :param timeout: 页面加载超时时间
    :param save_to_file: 保存结果的文件路径（可选）
    :return: 单个URL返回Dict，多个URL返回List[Dict]
    """
    # 统一处理为列表
    is_single_url = isinstance(urls, str)
    url_list = [urls] if is_single_url else urls
    
    if not url_list:
        return [] if not is_single_url else {}
    
    # 使用多标签页爬虫
    with MultitabWebSpider(headless=headless, timeout=timeout) as spider:
        results = spider.crawl_urls(url_list, max_tabs=max_tabs)
    
    # 保存到文件（如果指定）
    if save_to_file and results:
        with open(save_to_file, 'w', encoding='utf-8') as f:
            for result in results:
                json.dump(result, f, ensure_ascii=False)
                f.write('\n')
        logger.info(f"结果已保存到: {save_to_file}")
    
    # 返回结果
    if is_single_url:
        return results[0] if results else {
            'url': urls,
            'source_code': '',
            'status': 'failed',
            'error': 'No result',
            'content_length': 0
        }
    else:
        return results


@contextmanager
def get_spider(headless: bool = True, timeout: int = 30):
    """
    上下文管理器方式使用爬虫
    
    Usage:
        with get_spider() as spider:
            results = spider.crawl_urls(['http://example.com'])
    """
    spider = MultitabWebSpider(headless=headless, timeout=timeout)
    try:
        yield spider.__enter__()
    finally:
        spider.__exit__(None, None, None)


if __name__ == '__main__':
    # # 单站验证：chinattl
    # test_url = "http://www.chinattl.com/"
    # print("开始单站验证: ", test_url)

    # # 使用简化接口但限制为单标签（等效单站）
    # results = get_html_sources(
    #     [test_url],
    #     headless=False,
    #     max_tabs=1,
    #     timeout=30,
    #     save_to_file=None,
    # )

    # r = results[0] if results else {}
    # content_len = r.get('content_length', 0)
    # status = r.get('status')
    # print(f"结果: 状态={status}, 内容长度={content_len}")
    # if status != 'success':
    #     print("失败详情:", r)

    # 读取URL列表
    try:
        with open('urls.txt', 'r', encoding='utf-8') as f:
            url_list = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        # 如果没有urls.txt文件，使用示例URL
        url_list = [
            "https://www.baidu.com",
            "https://www.github.com",
            "https://www.google.com",
            "https://www.stackoverflow.com"
        ]
        logger.info("未找到urls.txt文件，使用示例URL进行测试")
    
    print(f"开始爬取 {len(url_list)} 个URL...")
    
    # 使用简化接口进行批量爬取
    results = get_html_sources(
        url_list,
        headless=False,  # 使用有头模式便于观察
        max_tabs=5,      # 最多同时5个标签页
        timeout=30,      # 30秒超时
        save_to_file='crawl_results.jsonl'
    )
    
    # 输出统计信息
    print(f"\n爬取完成！共处理 {len(results)} 个URL")
    success_count = len([r for r in results if r['status'] == 'success'])
    print(f"成功: {success_count}, 失败: {len(results) - success_count}")
    
    # 显示详细结果
    for result in results:
        url = result['url']
        status = result['status']
        length = result['content_length']
        print(f"URL: {url}")
        print(f"  状态: {status}, 内容长度: {length}")
        if status == 'failed' and 'error' in result:
            print(f"  错误: {result['error']}")
        print()
