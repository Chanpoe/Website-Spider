import json
import time
import os
import random
from queue import Queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Tuple, Union, Literal

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from loguru import logger


def get_html_source(
    url: Union[str, List[str]], 
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> Union[str, Tuple[str, int], List[Dict]]:
    """
    获取网页源码。
    提供多种获取策略和更强的反检测能力，专门针对政府网站等反爬严格的网站。
    
    :param url: 网页链接或链接列表
    :param headless: 是否优先使用无头模式
    :param return_status_code: 是否返回状态码。如果为True，返回(源码, 状态码)；否则只返回源码
    :param kwargs: 额外参数
        - max_workers: 多线程模式下的最大工作线程数，默认为5
        - timeout: 单个页面的超时时间，默认为60秒
        - user_agent: 自定义User-Agent
        - is_mobile: 是否模拟移动设备
        - result_path: 批量模式下的结果保存路径
        - enable_cdp: 是否启用CDP支持（用于获取状态码）
    :return: 
        - 单个URL: 根据return_status_code返回字符串或(字符串, 状态码)元组
        - URL列表: 返回结果字典列表
    """
    
    # 如果传入的是URL列表，使用多线程模式
    if isinstance(url, list):
        return _batch_get_html_sources(url, headless, return_status_code, **kwargs)
    
    # 单个URL处理
    return _get_single_html_source(url, headless, return_status_code, **kwargs)


def _create_driver(
    headless: bool = True, 
    user_agent: str = None, 
    is_mobile: bool = False,
    enable_cdp: bool = False
) -> uc.Chrome:
    """创建Chrome WebDriver实例（使用undetected-chromedriver）"""
    options = uc.ChromeOptions()
    
    # 基础设置
    if headless:
        options.add_argument("--headless")
        # undetected-chromedriver 在无头模式下需要额外设置
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    
    # 针对政府网站的特殊配置 - 更强的反检测能力
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
        "--start-maximized"
    ]
    
    # 如果需要CDP支持（用于获取状态码）
    if enable_cdp:
        options.add_argument("--enable-logging")
        options.add_argument("--log-level=0")
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    for arg in common_args:
        options.add_argument(arg)
    
    # 设置页面加载策略
    options.page_load_strategy = 'eager'
    
    # User Agent 设置
    # 注意：undetected-chromedriver 会自动处理 user-agent 以避免被检测
    # 只有在用户明确指定或需要移动设备模拟时才设置
    if user_agent:
        # 用户明确指定了 user-agent
        options.add_argument(f"--user-agent={user_agent}")
    elif is_mobile:
        # 移动设备模拟需要特定的 user-agent
        mobile_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
        options.add_argument(f"--user-agent={mobile_ua}")
    # 否则让 undetected-chromedriver 使用它自己的默认 user-agent
    
    # 移动设备模拟
    if is_mobile:
        mobile_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
        mobile_emulation = {
            "deviceMetrics": {"width": 375, "height": 667, "pixelRatio": 2.0},
            "userAgent": mobile_ua,
            "clientHints": {"mobile": True, "platform": "iOS", "platformVersion": "16.0"}
        }
        options.add_experimental_option("mobileEmulation", mobile_emulation)
    
    # 创建 undetected-chromedriver 实例
    try:
        # 尝试禁用 SSL 验证（仅用于下载驱动）
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
    except:
        pass
    
    driver = uc.Chrome(
        options=options,
        version_main=138,  # 指定Chrome版本为138
        use_subprocess=True,  # 使用子进程运行，提高稳定性
        driver_executable_path=None,  # 自动下载和管理驱动
        enable_cdp_events=enable_cdp  # 根据需要启用CDP事件支持
    )
    
    # 如果启用CDP，启用网络域
    if enable_cdp:
        try:
            driver.execute_cdp_cmd('Network.enable', {})
        except Exception as e:
            logger.debug(f"启用CDP Network域时出现异常（可忽略）: {e}")
    
    # undetected-chromedriver 已经内置了反检测机制，以下脚本作为额外保障
    try:
        driver.execute_script("""
            // 额外的反检测措施
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            
            // 更真实的 navigator 对象
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
        """)
    except Exception as e:
        logger.debug(f"执行额外反检测脚本时出现异常（可忽略）: {e}")
    
    return driver


def _get_status_code_from_logs(driver: uc.Chrome, target_url: str) -> int:
    """从浏览器日志中提取HTTP状态码"""
    try:
        # 方法1：通过CDP获取网络响应
        logs = driver.get_log('performance')
        for log in logs:
            message = json.loads(log['message'])
            if message['message']['method'] == 'Network.responseReceived':
                response = message['message']['params']['response']
                if response['url'] == target_url:
                    return response['status']
        
        # 方法2：通过JavaScript获取（备用方案）
        try:
            status_code = driver.execute_script("""
                return new Promise((resolve) => {
                    fetch(arguments[0])
                        .then(response => resolve(response.status))
                        .catch(() => resolve(0));
                });
            """, target_url)
            if status_code and status_code != 0:
                return status_code
        except:
            pass
        
        # 方法3：检查页面状态（最后的备用方案）
        try:
            # 如果页面能正常加载，通常状态码是200
            body = driver.find_element(By.TAG_NAME, "body")
            if body and len(driver.page_source) > 100:
                return 200
        except:
            pass
            
        return 0
        
    except Exception as e:
        logger.warning(f"获取状态码失败: {e}")
        return 0


def _get_single_html_source(
    url: str, 
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> Union[str, Tuple[str, int]]:
    """获取单个URL的HTML源码"""
    timeout = kwargs.get('timeout', 60)  # 默认超时时间为60秒
    user_agent = kwargs.get('user_agent')
    is_mobile = kwargs.get('is_mobile', False)
    enable_cdp = kwargs.get('enable_cdp', return_status_code)  # 如果需要状态码，自动启用CDP
    
    def _get_html_with_driver(target_url: str, use_headless: bool = True, retry_count: int = 0) -> Tuple[str, int]:
        """使用指定的浏览器模式获取HTML和状态码"""
        driver = None
        try:
            driver = _create_driver(use_headless, user_agent, is_mobile, enable_cdp)
            
            # 添加随机延迟
            time.sleep(random.uniform(1, 3))
            
            # 设置超时
            driver.set_page_load_timeout(timeout)
            driver.implicitly_wait(10)
            
            # 访问页面
            driver.get(target_url)
            
            # 获取状态码（如果需要）
            status_code = 0
            if return_status_code or enable_cdp:
                status_code = _get_status_code_from_logs(driver, target_url)
            
            # 等待页面加载
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except Exception as e:
                logger.warning(f"等待body元素超时: {e}")
            
            # 处理特殊阻止页面
            page_source = driver.page_source
            if 'Bitdefender Endpoint Security Tools 阻止了这个页面' in page_source:
                logger.info('Bitdefender Endpoint Security Tools 阻止了这个页面，正在点击跳过')
                try:
                    wait = WebDriverWait(driver, 10)
                    element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#takeMeThere a")))
                    element.click()
                    time.sleep(5)
                    page_source = driver.page_source
                    # 重新获取状态码
                    if return_status_code or enable_cdp:
                        status_code = _get_status_code_from_logs(driver, target_url)
                except Exception as skip_error:
                    logger.warning(f"点击跳过失败: {skip_error}")
            
            # 等待页面稳定
            time.sleep(3 + retry_count)
            
            # 滚动页面以触发懒加载内容
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(1)
            except Exception as scroll_error:
                logger.warning(f"页面滚动失败: {scroll_error}")
            
            # 获取最终的页面源码
            final_source = driver.page_source
            final_source = final_source.encode("utf8").decode()
            
            # 检查是否获取到有效内容
            if len(final_source.strip()) < 100:
                logger.warning(f"获取到的内容过少，可能被拦截: {len(final_source)} 字符")
                if retry_count < 2:  # 最多重试2次
                    logger.info(f"尝试第 {retry_count + 1} 次重试...")
                    driver.quit()
                    return _get_html_with_driver(target_url, use_headless, retry_count + 1)
            
            if return_status_code:
                logger.info(f'成功获取页面源码：{target_url}，长度：{len(final_source)}，状态码：{status_code}')
            else:
                logger.info(f'成功获取页面源码：{target_url}，长度：{len(final_source)}')
            
            return final_source, status_code
            
        except Exception as e:
            logger.error(f"获取页面时出错: {target_url}, 错误: {str(e)}")
            if retry_count < 2:
                logger.info(f"尝试第 {retry_count + 1} 次重试...")
                if driver:
                    driver.quit()
                return _get_html_with_driver(target_url, use_headless, retry_count + 1)
            return '', 0
        finally:
            if driver:
                driver.quit()
    
    def _try_with_different_strategies(target_url: str, preferred_headless: bool) -> Tuple[str, int]:
        """尝试不同的获取策略"""
        
        if preferred_headless:
            # 策略1: 无头模式
            try:
                logger.info("策略1: 使用无头模式 - headless=True")
                result, status_code = _get_html_with_driver(target_url, use_headless=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略1 headless=True失败: {e}")
                
            # 策略2: 有头模式
            try:
                logger.info("策略2: 使用有头模式 - headless=False")
                result, status_code = _get_html_with_driver(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略2 headless=False失败: {e}")
        else:
            # 如果不使用无头模式，仅使用有头模式
            try:
                logger.info("策略1: 使用有头模式 - headless=False")
                result, status_code = _get_html_with_driver(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略1 headless=False失败: {e}")
        
        return "", 0
    
    # 直接使用原始URL
    logger.info(f"尝试获取: {url}, headless模式: {headless}, 返回状态码: {return_status_code}")
    result, status_code = _try_with_different_strategies(url, headless)
    
    if not result:
        logger.error(f"所有策略均失败，无法获取源码: {url}")
        if return_status_code:
            return "", 0
        else:
            return ""
    
    # 根据参数返回不同格式的结果
    if return_status_code:
        return result, status_code
    else:
        return result


def _batch_get_html_sources(
    url_list: List[str], 
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> List[Dict]:
    """批量获取多个URL的HTML源码（多线程模式）"""
    max_workers = kwargs.get('max_workers', 5)
    result_path = kwargs.get('result_path', 'results.jsonl' if not return_status_code else 'results_with_status.jsonl')
    
    # 启动线程池进行爬取
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        logger.info(f'正在并发启动{max_workers}个Chrome实例，耗时较长请等待......')
        
        # 分配任务 - 保持顺序
        future_to_index = {}
        for index, url in enumerate(url_list):
            future = executor.submit(_get_single_html_source, url, headless, return_status_code, **kwargs)
            future_to_index[future] = index
        
        # 创建结果数组，保持原始顺序
        results = [None] * len(url_list)
        
        # 收集结果
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            url = url_list[index]
            try:
                # 设置超时
                timeout = kwargs.get('timeout', 60)
                result = future.result(timeout=timeout)
                
                if return_status_code:
                    source_code, status_code = result
                    result_dict = {'url': url, 'source_code': source_code, 'status_code': status_code}
                else:
                    source_code = result
                    result_dict = {'url': url, 'source_code': source_code}
                
                results[index] = result_dict
            except Exception as e:
                logger.error(f"Error for {url}: {str(e)}")
                if return_status_code:
                    result_dict = {'url': url, 'source_code': '', 'status_code': 0}
                else:
                    result_dict = {'url': url, 'source_code': ''}
                results[index] = result_dict
    
    # 按原始顺序写入JSONL文件
    with open(result_path, 'w', encoding='utf-8') as f:  # 使用'w'模式覆盖文件
        for result in results:
            json.dump(result, f, ensure_ascii=False)
            f.write("\n")  # 每个 JSON 对象占一行
    
    logger.info(f"所有任务已完成，结果已按原始顺序保存到 {result_path}")
    return results


if __name__ == '__main__':
    # # 示例：测试单个URL
    # test_url = "http://www.chinattl.com/"

    # print("=== 测试单个URL ===")
    # # 测试获取源码和状态码
    # print("\n=== 测试获取源码和状态码 ===")
    # html_source, status_code = get_html_source(test_url, headless=False, return_status_code=True)
    # if html_source:
    #     print(f"成功获取HTML源码，长度: {len(html_source)}，状态码: {status_code}")
    # else:
    #     print("获取HTML源码失败")

    # 示例：测试批量获取
    url_list = [
        "http://www.chinattl.com/",
        "https://www.gov.cn/",
    ]
    
    print("\n=== 测试批量获取（包含状态码） ===")
    results_with_status = get_html_source(
        url_list, 
        headless=False, 
        return_status_code=True,
        max_workers=2,
        result_path='test_results_with_status.jsonl'
    )
    
    print(f"\n批量处理完成，共处理 {len(results_with_status)} 个URL")
    for result in results_with_status:
        url = result['url']
        content_length = len(result['source_code'])
        status_code = result.get('status_code', 0)
        status = "成功" if content_length > 0 else "失败"
        print(f"URL: {url}")
        print(f"  状态: {status}, 内容长度: {content_length}, HTTP状态码: {status_code}")