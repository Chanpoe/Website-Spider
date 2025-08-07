import json
import time
import os
import random
import tempfile
import shutil
from queue import Queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Tuple, Union, Literal

from patchright.sync_api import sync_playwright
from loguru import logger


def get_html_source(
    url: Union[str, List[str]], 
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> Union[str, Tuple[str, int], List[Dict]]:
    """
    获取网页源码。
    使用 Patchright (undetected Playwright) 提供多种获取策略和更强的反检测能力，专门针对政府网站等反爬严格的网站。
    
    :param url: 网页链接或链接列表
    :param headless: 是否优先使用无头模式
    :param return_status_code: 是否返回状态码。如果为True，返回(源码, 状态码)；否则只返回源码
    :param kwargs: 额外参数
        - max_workers: 多线程模式下的最大工作线程数，默认为5
        - timeout: 单个页面的超时时间，默认为60秒
        - user_agent: 自定义User-Agent
        - is_mobile: 是否模拟移动设备
        - result_path: 批量模式下的结果保存路径
        - viewport: 视口大小设置，格式为 {"width": 1920, "height": 1080}
    :return: 
        - 单个URL: 根据return_status_code返回字符串或(字符串, 状态码)元组
        - URL列表: 返回结果字典列表
    """
    
    # 如果传入的是URL列表，使用多线程模式
    if isinstance(url, list):
        return _batch_get_html_sources(url, headless, return_status_code, **kwargs)
    
    # 单个URL处理
    return _get_single_html_source(url, headless, return_status_code, **kwargs)


def _create_persistent_context(
    playwright,
    headless: bool = True, 
    user_agent: str = None, 
    is_mobile: bool = False,
    viewport: dict = None
):
    """创建 Patchright 持久化浏览器上下文"""
    
    # 创建临时目录作为用户数据目录
    temp_dir = tempfile.mkdtemp(prefix="patchright_profile_")
    
    # 设置视口
    if viewport:
        viewport_size = viewport
    elif is_mobile:
        viewport_size = {"width": 375, "height": 667}
    else:
        viewport_size = {"width": 1920, "height": 1080}
    
    # 设置User-Agent
    if user_agent:
        ua = user_agent
    elif is_mobile:
        ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
    else:
        ua = None  # 让Patchright使用默认的反检测UA
    
    # 准备 launch_persistent_context 的参数
    context_options = {
        'user_data_dir': temp_dir,
        'headless': headless,
        'viewport': viewport_size,
        'locale': 'zh-CN',
        'timezone_id': 'Asia/Shanghai',
        'permissions': ['geolocation'],
        'geolocation': {'latitude': 39.9042, 'longitude': 116.4074},  # 北京坐标
        'extra_http_headers': {
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Cache-Control': 'max-age=0',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-User': '?1',
            'Sec-Fetch-Dest': 'document',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"macOS"',
            'Connection': 'keep-alive'
        },
        'args': [
            # 基础反检测参数
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
    }
    
    # 如果指定了User-Agent，添加到参数中
    if ua:
        context_options['user_agent'] = ua
    
    # 移动设备模拟
    if is_mobile:
        context_options['device_scale_factor'] = 2.0
        context_options['is_mobile'] = True
        context_options['has_touch'] = True

    # 启动持久化上下文 - Patchright 默认就是反检测的
    context = playwright.chromium.launch_persistent_context(**context_options)
    
    # 添加额外的反检测脚本
    context.add_init_script("""
        // 额外的反检测措施
        Object.defineProperty(navigator, 'languages', { 
            get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
        });
        Object.defineProperty(navigator, 'platform', { 
            get: () => 'Win32' 
        });
        
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
        
        // 隐藏webdriver痕迹
        delete Object.getPrototypeOf(navigator).webdriver;
        
        // 模拟真实的permissions
        if (navigator.permissions) {
            const originalQuery = navigator.permissions.query;
            navigator.permissions.query = function(parameters) {
                return originalQuery(parameters).then(result => {
                    if (parameters.name === 'notifications') {
                        result.state = 'prompt';
                    }
                    return result;
                });
            };
        }
        
        // 添加更多真实浏览器特征以避免412错误
        Object.defineProperty(navigator, 'doNotTrack', {
            get: () => null,
            configurable: true
        });
        
        // 模拟真实的网络连接
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                downlink: 10,
                rtt: 50,
                saveData: false
            }),
            configurable: true
        });
        
        // 模拟更真实的屏幕信息
        Object.defineProperty(screen, 'colorDepth', {
            get: () => 24,
            configurable: true
        });
        
        Object.defineProperty(screen, 'pixelDepth', {
            get: () => 24,
            configurable: true
        });
        
        // 重写fetch以添加更真实的请求头
        const originalFetch = window.fetch;
        window.fetch = function(...args) {
            if (args.length > 1 && args[1] && args[1].headers) {
                // 确保关键头部存在
                if (!args[1].headers['User-Agent']) {
                    args[1].headers['User-Agent'] = navigator.userAgent;
                }
                if (!args[1].headers['Referer']) {
                    args[1].headers['Referer'] = window.location.href;
                }
            }
            return originalFetch.apply(this, args);
        };
    """)
    
    return context, temp_dir


def _get_status_code_from_response(response) -> int:
    """从 Playwright 响应对象获取状态码"""
    try:
        return response.status if response else 0
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
    timeout = kwargs.get('timeout', 60) * 1000  # Playwright 使用毫秒
    user_agent = kwargs.get('user_agent')
    is_mobile = kwargs.get('is_mobile', False)
    viewport = kwargs.get('viewport')
    
    def _get_html_with_browser(target_url: str, use_headless: bool = True, retry_count: int = 0) -> Tuple[str, int]:
        """使用指定的浏览器模式获取HTML和状态码"""
        playwright_instance = None
        context = None
        temp_dir = None
        page = None
        
        try:
            playwright_instance = sync_playwright().start()
            context, temp_dir = _create_persistent_context(
                playwright_instance, use_headless, user_agent, is_mobile, viewport
            )

            if context.pages:
                page = context.pages[0]
            else:
                # 如果没有页面（极少见情况），则创建一个
                page = context.new_page()
            if not is_mobile:
                page.set_viewport_size({'width': 1920, 'height': 1080})
            
            # 先暂时注释掉请求拦截，测试基本功能
            # TODO: 修复请求拦截器
            # def handle_request(route, request):
            #     # 简单地继续所有请求
            #     route.continue_()
            # 
            # # 启用请求拦截
            # page.route("**/*", handle_request)

            # 添加随机延迟
            time.sleep(random.uniform(1, 3))
            
            # 设置超时
            page.set_default_timeout(timeout)
            page.set_default_navigation_timeout(timeout)
            
            # 监听响应以获取状态码
            response = None
            status_code = 0
            
            def handle_response(resp):
                nonlocal response, status_code
                if resp.url == target_url:
                    response = resp
                    status_code = resp.status
            
            if return_status_code:
                page.on("response", handle_response)
            
            # 访问页面 - 使用更稳定的等待策略
            main_response = page.goto(target_url, wait_until='domcontentloaded')
            
            # 如果没有通过监听获取到状态码，使用主响应的状态码
            if return_status_code and status_code == 0 and main_response:
                status_code = main_response.status
            
            # 等待页面稳定
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception as e:
                logger.warning(f"等待body元素超时: {e}")
            
            # 处理特殊阻止页面
            page_content = page.content()
            if 'Bitdefender Endpoint Security Tools 阻止了这个页面' in page_content:
                logger.info('Bitdefender Endpoint Security Tools 阻止了这个页面，正在点击跳过')
                try:
                    skip_element = page.wait_for_selector("#takeMeThere a", timeout=10000)
                    if skip_element:
                        skip_element.click()
                        page.wait_for_timeout(5000)
                        page_content = page.content()
                        # 重新获取状态码
                        if return_status_code:
                            try:
                                current_response = page.wait_for_response(
                                    lambda resp: resp.url == target_url, 
                                    timeout=5000
                                )
                                if current_response:
                                    status_code = current_response.status
                            except:
                                pass
                except Exception as skip_error:
                    logger.warning(f"点击跳过失败: {skip_error}")
            
            # 等待页面进一步稳定
            page.wait_for_timeout(3000 + retry_count * 1000)
            
            # 更完善的滚动策略以触发懒加载内容
            try:
                # 首先检查页面高度
                page_height = page.evaluate("document.body.scrollHeight")
                viewport_height = page.evaluate("window.innerHeight")
                
                logger.info(f"页面高度: {page_height}, 视口高度: {viewport_height}")
                
                # 分段滚动，确保所有图片都能被触发加载
                scroll_steps = max(3, int(page_height / viewport_height) + 1)
                for i in range(scroll_steps):
                    scroll_position = (page_height / scroll_steps) * i
                    page.evaluate(f"window.scrollTo(0, {scroll_position});")
                    page.wait_for_timeout(800)  # 每次滚动后等待800ms
                    
                    # 检查是否有新的图片开始加载
                    try:
                        page.wait_for_function(
                            "() => Array.from(document.images).filter(img => img.complete).length >= Array.from(document.images).length * 0.8",
                            timeout=2000
                        )
                    except:
                        pass  # 超时就继续，不阻塞流程
                
                # 滚动到底部
                page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                page.wait_for_timeout(1500)
                
                # 再次等待图片加载完成
                try:
                    page.wait_for_function(
                        "() => Array.from(document.images).every(img => img.complete)",
                        timeout=5000
                    )
                    logger.info("所有图片已加载完成")
                except:
                    logger.warning("等待图片加载超时，继续执行")
                
                # 最后滚动回顶部
                page.evaluate("window.scrollTo(0, 0);")
                page.wait_for_timeout(1000)
                
            except Exception as scroll_error:
                logger.warning(f"页面滚动失败: {scroll_error}")
            
            # 获取最终的页面源码
            final_source = page.content()
            
            # 检查是否获取到有效内容
            if len(final_source.strip()) < 100:
                logger.warning(f"获取到的内容过少，可能被拦截: {len(final_source)} 字符")
                if retry_count < 2:  # 最多重试2次
                    logger.info(f"尝试第 {retry_count + 1} 次重试...")
                    return _get_html_with_browser(target_url, use_headless, retry_count + 1)
            
            if return_status_code:
                logger.info(f'成功获取页面源码：{target_url}，长度：{len(final_source)}，状态码：{status_code}')
            else:
                logger.info(f'成功获取页面源码：{target_url}，长度：{len(final_source)}')
            
            return final_source, status_code
            
        except Exception as e:
            logger.error(f"获取页面时出错: {target_url}, 错误: {str(e)}")
            if retry_count < 2:
                logger.info(f"尝试第 {retry_count + 1} 次重试...")
                return _get_html_with_browser(target_url, use_headless, retry_count + 1)
            return '', 0
        finally:
            # 清理资源
            try:
                if page:
                    page.close()
                if context:
                    context.close()
                if playwright_instance:
                    playwright_instance.stop()
                # 清理临时目录
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as cleanup_error:
                        logger.warning(f"清理临时目录失败: {cleanup_error}")
            except Exception as cleanup_error:
                logger.warning(f"清理资源时出错: {cleanup_error}")
    
    def _try_with_different_strategies(target_url: str, preferred_headless: bool) -> Tuple[str, int]:
        """尝试不同的获取策略"""
        
        if preferred_headless:
            # 策略1: 无头模式
            try:
                logger.info("策略1: 使用无头模式 - headless=True")
                result, status_code = _get_html_with_browser(target_url, use_headless=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略1 headless=True失败: {e}")
                
            # 策略2: 有头模式
            try:
                logger.info("策略2: 使用有头模式 - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略2 headless=False失败: {e}")
        else:
            # 如果不使用无头模式，仅使用有头模式
            try:
                logger.info("策略1: 使用有头模式 - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False)
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
        logger.info(f'正在并发启动{max_workers}个Patchright实例，耗时较长请等待......')
        
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
    # 示例：测试单个URL
    test_url = "http://www.chinattl.com/"

    print("=== 测试单个URL (Patchright版本) ===")
    # 测试获取源码和状态码
    print("\n=== 测试获取源码和状态码 ===")
    html_source, status_code = get_html_source(test_url, headless=False, return_status_code=True)
    time.sleep(10)
    if html_source:
        print(f"成功获取HTML源码，长度: {len(html_source)}，状态码: {status_code}")
    else:
        print("获取HTML源码失败")

    # # 示例：测试批量获取
    # url_list = [
    #     "https://www.gov.cn/",
    #     "https://bot.sannysoft.com/",
    # ]
    #
    # print("\n=== 测试批量获取（包含状态码） ===")
    # results_with_status = get_html_source(
    #     url_list,
    #     headless=True,  # 批量测试使用无头模式以提高速度
    #     return_status_code=True,
    #     max_workers=2,
    #     result_path='patchright_test_results_with_status.jsonl'
    # )
    #
    # print(f"\n批量处理完成，共处理 {len(results_with_status)} 个URL")
    # for result in results_with_status:
    #     url = result['url']
    #     content_length = len(result['source_code'])
    #     status_code = result.get('status_code', 0)
    #     status = "成功" if content_length > 0 else "失败"
    #     print(f"URL: {url}")
    #     print(f"  状态: {status}, 内容长度: {content_length}, HTTP状态码: {status_code}")
