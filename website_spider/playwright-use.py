import json
import os
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from urllib.parse import urlparse
from typing import List, Optional, Dict, Tuple, Union

from playwright.sync_api import sync_playwright
from undetected_playwright import Tarnished
from loguru import logger


def _get_status_code_from_response(page, target_url: str) -> int:
    """从页面响应中获取状态码"""
    try:
        # 方法1：通过 JavaScript 检查页面状态
        status_code = page.evaluate("""
            () => {
                if (window.performance && window.performance.getEntriesByType) {
                    const navigationEntries = window.performance.getEntriesByType('navigation');
                    if (navigationEntries.length > 0) {
                        return navigationEntries[0].responseStatus || 200;
                    }
                }
                return 200;  // 默认返回200，表示页面已成功加载
            }
        """)
        return status_code if status_code else 200
    except Exception as e:
        logger.debug(f"获取状态码失败: {e}")
        return 200  # 默认返回200


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
    :return: 
        - 单个URL: 根据return_status_code返回字符串或(字符串, 状态码)元组
        - URL列表: 返回结果字典列表
    """
    
    # 如果传入的是URL列表，使用多线程模式
    if isinstance(url, list):
        return _batch_get_html_sources(url, headless, return_status_code, **kwargs)
    
    # 单个URL处理
    return _get_single_html_source(url, headless, return_status_code, **kwargs)


def _get_single_html_source(
    url: str, 
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> Union[str, Tuple[str, int]]:
    """获取单个URL的HTML源码"""
    timeout = kwargs.get('timeout', 60)
    user_agent = kwargs.get('user_agent')
    is_mobile = kwargs.get('is_mobile', False)

    def _get_html_with_browser(target_url, use_headless=True, user_agent=None, is_mobile=False, retry_count=0) -> Tuple[str, int]:
        """内部函数：使用指定的浏览器模式获取HTML和状态码"""
        with sync_playwright() as p:
            # 改进的浏览器参数，减少被检测的风险
            common_browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-sync",
                "--disable-translate", 
                "--disable-default-apps",
                "--disable-component-update",
                "--disable-domain-reliability",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-hang-monitor",
                "--disable-prompt-on-repost",
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-component-extensions-with-background-pages",
                "--disable-ipc-flooding-protection",
                "--no-first-run",
                "--mute-audio",
                "--hide-scrollbars",
                # 移除可能被检测的参数
                # "--disable-web-security",  # 这个参数容易被检测
                # "--disable-gpu",  # 不禁用GPU，保持正常渲染
            ]

            # 创建临时用户数据目录
            temp_user_data_dir = tempfile.mkdtemp(prefix='playwright_')
            browser = p.chromium.launch_persistent_context(
                headless=use_headless,
                user_data_dir=temp_user_data_dir,
                args=common_browser_args,
                ignore_default_args=['--enable-automation'],  # 禁用自动化标志
                ignore_https_errors=True,  # 忽略HTTPS错误
                bypass_csp=True,  # 绕过内容安全策略
            )

            # 更真实的UA配置 - 使用常见的真实浏览器版本
            default_ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
            mobile_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1'

            # 根据参数选择UA
            if user_agent:
                selected_ua = user_agent
            elif is_mobile:
                selected_ua = mobile_ua
            else:
                selected_ua = default_ua

            # 更真实的浏览器上下文配置
            context_config = {
                'user_agent': selected_ua,
                'is_mobile': is_mobile,
                'has_touch': is_mobile,
                'device_scale_factor': 2 if is_mobile else 1,
                'locale': "zh-CN",
                'timezone_id': "Asia/Shanghai",
                'extra_http_headers': {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Cache-Control": "max-age=0",
                    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
                    "Sec-Ch-Ua-Mobile": "?0" if not is_mobile else "?1",
                    "Sec-Ch-Ua-Platform": '"macOS"' if not is_mobile else '"iOS"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Ch-Ua-Full-Version-List": '"Not A(Brand";v="99.0.0.0", "Google Chrome";v="121.0.6167.160", "Chromium";v="121.0.6167.160"'
                }
            }

            # 更完整的反检测脚本，修复 WebGL 检测问题
            stealth_script = """
            // 修复 WebGL 检测
            (() => {
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.'; // UNMASKED_VENDOR_WEBGL
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
                    return getParameter.call(this, parameter);
                };
                
                const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.'; // UNMASKED_VENDOR_WEBGL
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
                    return getParameter2.call(this, parameter);
                };
                
                // 确保 WebGL 上下文可用
                const originalGetContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(contextType, contextAttributes) {
                    if (contextType === 'webgl' || contextType === 'experimental-webgl') {
                        const context = originalGetContext.call(this, contextType, contextAttributes);
                        if (context) {
                            // 重写 getParameter 方法
                            const originalGetParameter = context.getParameter;
                            context.getParameter = function(parameter) {
                                if (parameter === context.UNMASKED_VENDOR_WEBGL || parameter === 37445) {
                                    return 'Intel Inc.';
                                }
                                if (parameter === context.UNMASKED_RENDERER_WEBGL || parameter === 37446) {
                                    return 'Intel Iris OpenGL Engine';
                                }
                                return originalGetParameter.call(this, parameter);
                            };
                        }
                        return context;
                    }
                    return originalGetContext.call(this, contextType, contextAttributes);
                };
                
                // 更彻底地隐藏 webdriver 属性
                try {
                    // 方法1: 重定义 webdriver 属性
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                        set: () => {},
                        configurable: true,
                        enumerable: false
                    });
                } catch (e) {}
                
                try {
                    // 方法2: 删除原型链上的 webdriver
                    delete navigator.__proto__.webdriver;
                    delete Navigator.prototype.webdriver;
                } catch (e) {}
                
                try {
                    // 方法3: 从 navigator 对象中完全删除
                    delete navigator.webdriver;
                } catch (e) {}
                
                // 额外的反检测措施
                try {
                    // 隐藏 Chrome DevTools Protocol
                    if (window.chrome && window.chrome.runtime) {
                        Object.defineProperty(window.chrome.runtime, 'onConnect', {
                            get: () => undefined,
                            configurable: true
                        });
                    }
                } catch (e) {}
                
                try {
                    // 修改 permissions API
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ? 
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                    );
                } catch (e) {}
                
                try {
                    // 伪造更真实的浏览器指纹
                    Object.defineProperty(navigator, 'platform', {
                        get: () => 'MacIntel',
                        configurable: true
                    });
                    
                    Object.defineProperty(navigator, 'hardwareConcurrency', {
                        get: () => 8,
                        configurable: true
                    });
                    
                    Object.defineProperty(navigator, 'deviceMemory', {
                        get: () => 8,
                        configurable: true
                    });
                } catch (e) {}
                
                try {
                    // 隐藏自动化相关的 window 属性
                    Object.defineProperty(window, 'outerHeight', {
                        get: () => window.innerHeight,
                        configurable: true
                    });
                    
                    Object.defineProperty(window, 'outerWidth', {
                        get: () => window.innerWidth,
                        configurable: true
                    });
                } catch (e) {}
                
                try {
                    // 添加随机鼠标移动事件来模拟真实用户
                    const addRandomMouseEvents = () => {
                        const randomMove = () => {
                            const x = Math.random() * window.innerWidth;
                            const y = Math.random() * window.innerHeight;
                            const event = new MouseEvent('mousemove', {
                                clientX: x,
                                clientY: y,
                                bubbles: true
                            });
                            document.dispatchEvent(event);
                        };
                        
                        // 随机移动鼠标
                        setTimeout(() => {
                            for (let i = 0; i < 3; i++) {
                                setTimeout(randomMove, i * 200);
                            }
                        }, Math.random() * 2000 + 1000);
                    };
                    
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', addRandomMouseEvents);
                    } else {
                        addRandomMouseEvents();
                    }
                } catch (e) {}
            })();
            """

            # 如果是移动设备，调整viewport
            if is_mobile:
                context_config['viewport'] = {'width': 375, 'height': 667}

            # context = browser.new_context(**context_config)

            context = browser
            
            # 应用 undetected-playwright 的反检测功能
            Tarnished.apply_stealth(context)

            # 使用持久化上下文默认提供的页面，避免创建新页面
            # 持久化上下文通常自带一个空白页面，直接使用它
            if context.pages:
                page = context.pages[0] 
            else:
                # 如果没有页面（极少见情况），则创建一个
                page = context.new_page()
            if not is_mobile:
                page.set_viewport_size({'width': 1920, 'height': 1080})

            page.set_extra_http_headers({
                "User-Agent": selected_ua,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1"
            })
            # 注入增强的反检测脚本（补充 undetected-playwright 的功能）
            page.add_init_script(stealth_script)

            # 设置更高效的等待策略
            try:
                # 添加随机延迟
                time.sleep(random.uniform(1, 3))

                # 首先尝试10秒内加载domcontentloaded
                try:
                    page.goto(target_url, timeout=10000, wait_until='domcontentloaded')
                    logger.info("domcontentloaded加载成功")
                except Exception as e:
                    logger.warning(f"domcontentloaded超时: {e}")
                    # 检查当前页面是否有内容
                    try:
                        current_html = page.content()
                        if len(current_html.strip()) > 100:
                            logger.info(f"domcontentloaded超时但页面有内容，直接返回: {len(current_html)} 字符")
                            # 获取状态码
                            status_code = _get_status_code_from_response(page, target_url)
                            browser.close()
                            # 清理临时用户数据目录
                            try:
                                import shutil
                                shutil.rmtree(temp_user_data_dir, ignore_errors=True)
                            except:
                                pass
                            return current_html, status_code
                    except Exception as content_error:
                        logger.warning(f"获取当前页面内容失败: {content_error}")

                # 等待3秒让页面稳定
                time.sleep(3 + retry_count)

                # 检查页面是否有有效内容
                try:
                    # 等待body元素出现
                    page.wait_for_selector('body', timeout=3000)

                    # 检查页面是否有内容
                    body_text = page.evaluate("document.body ? document.body.innerText : ''")
                    if len(body_text.strip()) > 50:  # 如果有文本内容，说明页面已加载
                        logger.info(f"页面已加载，检测到文本内容长度: {len(body_text)}")
                    else:
                        logger.warning("页面内容为空，可能未正确加载")

                except Exception as e:
                    logger.warning(f"检查页面内容时出错: {e}")

                # 滚动页面以触发懒加载内容
                try:
                    page.evaluate("""
                        window.scrollTo(0, document.body.scrollHeight);
                        setTimeout(() => window.scrollTo(0, 0), 500);
                    """)
                    time.sleep(1)
                except:
                    logger.warning("页面滚动失败")

                # 获取整个 HTML 源码
                html = page.content()

                # 检查是否获取到有效内容
                if len(html.strip()) < 100:  # 如果内容太少，可能是被拦截
                    logger.warning(f"获取到的内容过少，可能被拦截: {len(html)} 字符")
                    # 再等待5秒，再次检查
                    logger.info("内容过少，等待5秒后再次检查...")
                    time.sleep(5)
                    html = page.content()
                    if len(html.strip()) < 100:
                        logger.warning("等待5秒后内容仍然过少，准备重试或切换策略")
                        if retry_count < 2:  # 最多重试2次
                            logger.info(f"尝试第 {retry_count + 1} 次重试...")
                            return _get_html_with_browser(target_url, use_headless, user_agent, is_mobile,
                                                          retry_count + 1)

                # 获取状态码
                status_code = _get_status_code_from_response(page, target_url)
                browser.close()
                # 清理临时用户数据目录
                try:
                    import shutil
                    shutil.rmtree(temp_user_data_dir, ignore_errors=True)
                except:
                    pass
                return html, status_code

            except Exception as e:
                logger.warning(f"页面加载失败: {e}")

                # 如果是超时错误，尝试获取当前页面内容
                if "Timeout" in str(e):
                    logger.info("检测到超时错误，尝试获取当前页面内容...")
                    try:
                        # 检查当前页面是否有内容
                        current_html = page.content()
                        if len(current_html.strip()) > 100:
                            logger.info(f"超时但页面有内容，返回当前内容: {len(current_html)} 字符")
                            # 获取状态码
                            status_code = _get_status_code_from_response(page, target_url)
                            browser.close()
                            # 清理临时用户数据目录
                            try:
                                import shutil
                                shutil.rmtree(temp_user_data_dir, ignore_errors=True)
                            except:
                                pass
                            return current_html, status_code
                    except Exception as content_error:
                        logger.warning(f"获取当前页面内容失败: {content_error}")

                if retry_count < 2:
                    logger.info(f"尝试第 {retry_count + 1} 次重试...")
                    return _get_html_with_browser(target_url, use_headless, user_agent, is_mobile, retry_count + 1)
                else:
                    browser.close()
                    # 清理临时用户数据目录
                    try:
                        import shutil
                        shutil.rmtree(temp_user_data_dir, ignore_errors=True)
                    except:
                        pass
                    return "", 0

    def _try_with_different_strategies(target_url, preferred_headless) -> Tuple[str, int]:
        """尝试不同的获取策略"""
        
        if preferred_headless:
            # 如果优先使用无头模式，先尝试无头，失败后尝试有头
            # 策略1: 默认UA (Windows Chrome) - 无头模式
            try:
                logger.info("策略1: 使用默认UA (Windows Chrome) - headless=True")
                result, status_code = _get_html_with_browser(target_url, use_headless=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略1 headless=True失败: {e}")
                
            # 策略2: 手机UA - 无头模式
            try:
                logger.info("策略2: 使用手机UA模式 - headless=True")
                result, status_code = _get_html_with_browser(target_url, use_headless=True, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略2 headless=True失败: {e}")
                
            # 如果无头模式都失败，再尝试有头模式
            logger.info("无头模式失败，尝试有头模式...")
            
            # 策略3: 默认UA (Windows Chrome) - 有头模式
            try:
                logger.info("策略3: 使用默认UA (Windows Chrome) - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略3成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略3 headless=False失败: {e}")
                
            # 策略4: 手机UA - 有头模式
            try:
                logger.info("策略4: 使用手机UA模式 - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略4成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略4 headless=False失败: {e}")
        else:
            # 如果不使用无头模式，仅使用有头模式
            # 策略1: 默认UA (Windows Chrome) - 有头模式
            try:
                logger.info("策略1: 使用默认UA (Windows Chrome) - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略1 headless=False失败: {e}")
                
            # 策略2: 手机UA - 有头模式
            try:
                logger.info("策略2: 使用手机UA模式 - headless=False")
                result, status_code = _get_html_with_browser(target_url, use_headless=False, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result, status_code
            except Exception as e:
                logger.warning(f"策略2 headless=False失败: {e}")

        return "", 0

    # 直接使用原始URL，不进行https/http切换
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
    result_path = kwargs.get('result_path', 'playwright_results.jsonl' if not return_status_code else 'playwright_results_with_status.jsonl')
    
    # 启动线程池进行爬取
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        logger.info(f'正在并发启动{max_workers}个Playwright实例，耗时较长请等待......')
        
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

    print("=== 测试单个URL ===")
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
    #     "http://www.chinattl.com/",
    #     "https://www.gov.cn/",
    # ]
    #
    # print("\n=== 测试批量获取（包含状态码） ===")
    # results_with_status = get_html_source(
    #     url_list,
    #     headless=False,
    #     return_status_code=True,
    #     max_workers=2,
    #     result_path='playwright_test_results_with_status.jsonl'
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