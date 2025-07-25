import time

from playwright.sync_api import sync_playwright
from loguru import logger


def get_html_source(url, headless=True) -> str:
    """
    获取网页源码。
    如果url为http协议，会优先尝试https。
    如果无头模式失败，会自动尝试有头模式。
    提供多种获取策略和更强的反检测能力，专门针对政府网站等反爬严格的网站。
    如果所有尝试都失败，将返回空字符串，而不是抛出异常。

    :param url: 网页链接
    :param headless: 是否优先使用无头模式
    :return: 网页源码，如果失败则返回空字符串
    """

    def _get_html_with_browser(target_url, use_headless=True, user_agent=None, is_mobile=False, retry_count=0):
        """内部函数：使用指定的浏览器模式获取HTML"""
        with sync_playwright() as p:
            # 针对政府网站的特殊配置
            common_browser_args = [
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",  # 避免共享内存问题
                "--no-sandbox",  # 某些政府网站需要
                "--disable-setuid-sandbox",
                "--disable-gpu",  # 禁用GPU加速
                "--disable-software-rasterizer",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=TranslateUI",
                "--disable-ipc-flooding-protection",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-plugins",
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
                "--disable-background-upload"
            ]

            # 针对政府网站，使用更真实的浏览器配置
            browser = p.chromium.launch(
                headless=use_headless,
                args=common_browser_args,
                ignore_default_args=['--enable-automation']  # 禁用自动化标志
            )

            # 默认UA配置
            default_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            mobile_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'

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
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "max-age=0",
                    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    "Sec-Ch-Ua-Mobile": "?0" if not is_mobile else "?1",
                    "Sec-Ch-Ua-Platform": '"Windows"' if not is_mobile else '"iOS"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1"
                }
            }

            # 如果是移动设备，调整viewport
            if is_mobile:
                context_config['viewport'] = {'width': 375, 'height': 667}

            context = browser.new_context(**context_config)

            # 更全面的反检测脚本
            context.add_init_script("""
                // 基础反检测
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'connection', { get: () => ({ downlink: 10, effectiveType: '4g', rtt: 50 }) });

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

                // 模拟真实的 screen 对象
                Object.defineProperty(window, 'screen', {
                    get: () => ({
                        width: 1920,
                        height: 1080,
                        availWidth: 1920,
                        availHeight: 1040,
                        colorDepth: 24,
                        pixelDepth: 24,
                        orientation: { type: 'landscape-primary', angle: 0 }
                    })
                });

                // 模拟真实的 window 对象
                Object.defineProperty(window, 'outerWidth', { get: () => 1920 });
                Object.defineProperty(window, 'outerHeight', { get: () => 1040 });
                Object.defineProperty(window, 'innerWidth', { get: () => 1920 });
                Object.defineProperty(window, 'innerHeight', { get: () => 937 });

                // 禁用 WebGL 指纹检测
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    if (parameter === 37447) return 'Intel Iris OpenGL Engine';
                    return getParameter.call(this, parameter);
                };

                // 模拟真实的 Canvas 指纹
                const originalGetContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(type, attributes) {
                    const context = originalGetContext.call(this, type, attributes);
                    if (type === '2d') {
                        const originalFillText = context.fillText;
                        context.fillText = function(text, x, y, maxWidth) {
                            return originalFillText.call(this, text, x, y, maxWidth);
                        };
                    }
                    return context;
                };

                // 模拟真实的 AudioContext
                if (window.AudioContext || window.webkitAudioContext) {
                    const AudioContext = window.AudioContext || window.webkitAudioContext;
                    const originalGetChannelData = AudioContext.prototype.getChannelData;
                    AudioContext.prototype.getChannelData = function(channel) {
                        const data = originalGetChannelData.call(this, channel);
                        // 添加微小的随机变化
                        for (let i = 0; i < data.length; i += 100) {
                            data[i] += Math.random() * 0.0001;
                        }
                        return data;
                    };
                }

                // 模拟真实的 Battery API
                if (navigator.getBattery) {
                    const originalGetBattery = navigator.getBattery;
                    navigator.getBattery = function() {
                        return Promise.resolve({
                            charging: true,
                            chargingTime: Infinity,
                            dischargingTime: Infinity,
                            level: 0.85
                        });
                    };
                }

                // 模拟真实的 Permissions API
                if (navigator.permissions) {
                    const originalQuery = navigator.permissions.query;
                    navigator.permissions.query = function(permissionDesc) {
                        return Promise.resolve({ state: 'granted' });
                    };
                }

                // 禁用各种检测
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

                // 模拟真实的 Chrome 运行时
                if (window.chrome && window.chrome.runtime) {
                    Object.defineProperty(window.chrome.runtime, 'onConnect', { get: () => undefined });
                    Object.defineProperty(window.chrome.runtime, 'onMessage', { get: () => undefined });
                }

                // 模拟真实的 Performance API
                if (window.performance && window.performance.getEntriesByType) {
                    const originalGetEntriesByType = window.performance.getEntriesByType;
                    window.performance.getEntriesByType = function(entryType) {
                        const entries = originalGetEntriesByType.call(this, entryType);
                        // 添加一些随机性
                        return entries.map(entry => ({
                            ...entry,
                            duration: entry.duration + Math.random() * 0.1
                        }));
                    };
                }
            """)

            page = context.new_page()
            if not is_mobile:
                page.set_viewport_size({'width': 1920, 'height': 1080})

            # 设置更高效的等待策略
            try:
                # 添加随机延迟
                import random
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
                            browser.close()
                            return current_html
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

                browser.close()
                return html

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
                            browser.close()
                            return current_html
                    except Exception as content_error:
                        logger.warning(f"获取当前页面内容失败: {content_error}")

                if retry_count < 2:
                    logger.info(f"尝试第 {retry_count + 1} 次重试...")
                    return _get_html_with_browser(target_url, use_headless, user_agent, is_mobile, retry_count + 1)
                else:
                    browser.close()
                    raise e

    def _try_with_different_strategies(target_url, preferred_headless):
        """尝试不同的获取策略"""
        
        if preferred_headless:
            # 如果优先使用无头模式，先尝试无头，失败后尝试有头
            # 策略1: 默认UA (Windows Chrome) - 无头模式
            try:
                logger.info("策略1: 使用默认UA (Windows Chrome) - headless=True")
                result = _get_html_with_browser(target_url, use_headless=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略1 headless=True失败: {e}")
                
            # 策略2: 手机UA - 无头模式
            try:
                logger.info("策略2: 使用手机UA模式 - headless=True")
                result = _get_html_with_browser(target_url, use_headless=True, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略2 headless=True失败: {e}")
                
            # 如果无头模式都失败，再尝试有头模式
            logger.info("无头模式失败，尝试有头模式...")
            
            # 策略3: 默认UA (Windows Chrome) - 有头模式
            try:
                logger.info("策略3: 使用默认UA (Windows Chrome) - headless=False")
                result = _get_html_with_browser(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略3成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略3 headless=False失败: {e}")
                
            # 策略4: 手机UA - 有头模式
            try:
                logger.info("策略4: 使用手机UA模式 - headless=False")
                result = _get_html_with_browser(target_url, use_headless=False, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略4成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略4 headless=False失败: {e}")
        else:
            # 如果不使用无头模式，仅使用有头模式
            # 策略1: 默认UA (Windows Chrome) - 有头模式
            try:
                logger.info("策略1: 使用默认UA (Windows Chrome) - headless=False")
                result = _get_html_with_browser(target_url, use_headless=False)
                if result and len(result.strip()) > 100:
                    logger.info("策略1成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略1 headless=False失败: {e}")
                
            # 策略2: 手机UA - 有头模式
            try:
                logger.info("策略2: 使用手机UA模式 - headless=False")
                result = _get_html_with_browser(target_url, use_headless=False, is_mobile=True)
                if result and len(result.strip()) > 100:
                    logger.info("策略2成功获取到内容")
                    return result
            except Exception as e:
                logger.warning(f"策略2 headless=False失败: {e}")

        return ""

    # 直接使用原始URL，不进行https/http切换
    logger.info(f"尝试获取: {url}, headless模式: {headless}")
    result = _try_with_different_strategies(url, headless)
    if result:
        return result

    logger.error(f"所有策略均失败，无法获取源码: {url}")
    return ""


if __name__ == '__main__':
    test_url = "https://baike.baidu.com/item/hello%20world/85501?fromtitle=helloworld"  # 替换为你要测试的URL
    html_source = get_html_source(test_url, headless=True)
    if html_source:
        print(html_source)
        print(f"成功获取HTML源码，长度: {len(html_source)}")
    else:
        print("获取HTML源码失败")