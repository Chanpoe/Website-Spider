import json
import time
import os
import random
import tempfile
import shutil
import asyncio
from typing import List, Dict, Tuple, Union

from patchright.async_api import async_playwright
from loguru import logger


def get_html_source(
    url: Union[str, List[str]],
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> Union[str, Tuple[str, int], List[Dict]]:
    """
    获取网页源码（保持原有对外接口不变）。
    
    - 单个 URL：内部用单实例单页面抓取
    - URL 列表：使用“单实例多标签页（单上下文多 page）并发”，并发度由 max_tabs 控制
    
    可选 kwargs：
        - max_tabs: 最大并发标签页（默认 5）
        - timeout: 单页面超时（秒，默认 60）
        - user_agent: 自定义 UA
        - is_mobile: 是否模拟移动设备（默认 False）
        - result_path: 批量模式结果保存路径（JSONL）
        - viewport: 视口大小 {"width": 1920, "height": 1080}
    """

    if isinstance(url, list):
        return _batch_get_html_sources(url, headless, return_status_code, **kwargs)

    return _get_single_html_source(url, headless, return_status_code, **kwargs)


async def _create_persistent_context_async(
    playwright,
    headless: bool = True,
    user_agent: str = None,
    is_mobile: bool = False,
    viewport: dict = None
):
    """创建 Patchright 持久化浏览器上下文（异步版）。返回 (context, temp_dir)。"""

    temp_dir = tempfile.mkdtemp(prefix="patchright_profile_")

    if viewport:
        viewport_size = viewport
    elif is_mobile:
        viewport_size = {"width": 375, "height": 667}
    else:
        # 更小的默认窗口，避免在本地屏幕放不下
        viewport_size = {"width": 1280, "height": 800}

    if user_agent:
        ua = user_agent
    elif is_mobile:
        ua = (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 '
            'Mobile/15E148 Safari/604.1'
        )
    else:
        ua = None

    context_options = {
        'user_data_dir': temp_dir,
        'headless': headless,
        'viewport': viewport_size,
        'locale': 'zh-CN',
        'timezone_id': 'Asia/Shanghai',
        'permissions': ['geolocation'],
        'geolocation': {'latitude': 39.9042, 'longitude': 116.4074},
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
            # 根据视口动态设置窗口尺寸
            f"--window-size={viewport_size['width']},{viewport_size['height']}",
        ],
    }

    if ua:
        context_options['user_agent'] = ua

    if is_mobile:
        context_options['device_scale_factor'] = 2.0
        context_options['is_mobile'] = True
        context_options['has_touch'] = True

    context = await playwright.chromium.launch_persistent_context(**context_options)

    await context.add_init_script(
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
        delete Object.getPrototypeOf(navigator).webdriver;
        if (navigator.permissions) {
          const originalQuery = navigator.permissions.query;
          navigator.permissions.query = function (parameters) {
            return originalQuery(parameters).then(result => {
              if (parameters.name === 'notifications') result.state = 'prompt';
              return result;
            });
          };
        }
        Object.defineProperty(navigator, 'doNotTrack', { get: () => null, configurable: true });
        Object.defineProperty(navigator, 'connection', {
          get: () => ({ effectiveType: '4g', downlink: 10, rtt: 50, saveData: false }),
          configurable: true
        });
        Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
        Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
        const originalFetch = window.fetch;
        window.fetch = function (...args) {
          if (args.length > 1 && args[1] && args[1].headers) {
            if (!args[1].headers['User-Agent']) args[1].headers['User-Agent'] = navigator.userAgent;
            if (!args[1].headers['Referer']) args[1].headers['Referer'] = window.location.href;
          }
          return originalFetch.apply(this, args);
        };
        """
    )

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
    """获取单个URL的HTML源码（内部启一个上下文单页面）。"""

    timeout_ms = kwargs.get('timeout', 60) * 1000
    user_agent = kwargs.get('user_agent')
    is_mobile = kwargs.get('is_mobile', False)
    viewport = kwargs.get('viewport')
    effective_viewport = viewport or ({'width': 375, 'height': 667} if is_mobile else {'width': 1280, 'height': 800})

    async def _run_once() -> Tuple[str, int]:
        playwright_instance = await async_playwright().start()
        context = None
        temp_dir = None
        page = None
        try:
            context, temp_dir = await _create_persistent_context_async(
                playwright_instance, headless, user_agent, is_mobile, viewport
            )
            if context.pages:
                page = context.pages[0]
            else:
                page = await context.new_page()
            try:
                await page.set_viewport_size(effective_viewport)
            except Exception:
                pass

            await asyncio.sleep(random.uniform(1, 2.5))

            try:
                page.set_default_timeout(timeout_ms)
                page.set_default_navigation_timeout(timeout_ms)
            except Exception:
                pass

            status_code = 0
            main_response = None
            try:
                main_response = await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
            except Exception as nav_err:
                logger.warning(f"导航超时或错误: {url}, {nav_err}")

            if return_status_code and main_response:
                status_code = main_response.status

            try:
                await page.wait_for_selector("body", timeout=10000)
            except Exception as e:
                logger.warning(f"等待body元素超时: {e}")

            page_content = await page.content()
            if 'Bitdefender Endpoint Security Tools 阻止了这个页面' in page_content:
                logger.info('检测到Bitdefender阻止页，尝试跳过')
                try:
                    skip_element = await page.wait_for_selector("#takeMeThere a", timeout=10000)
                    if skip_element:
                        await skip_element.click()
                        await page.wait_for_timeout(5000)
                        page_content = await page.content()
                        if return_status_code and not status_code:
                            try:
                                current_response = await page.wait_for_response(lambda resp: resp.url == url, timeout=5000)
                                if current_response:
                                    status_code = current_response.status
                            except Exception:
                                pass
                except Exception as skip_error:
                    logger.warning(f"点击跳过失败: {skip_error}")

            await page.wait_for_timeout(1500)

            try:
                page_height = await page.evaluate("document.body.scrollHeight")
                viewport_height = await page.evaluate("window.innerHeight")
                scroll_steps = max(3, int(page_height / max(1, viewport_height)) + 1)
                for i in range(scroll_steps):
                    scroll_position = (page_height / scroll_steps) * i
                    await page.evaluate("(y) => window.scrollTo(0, y)", scroll_position)
                    await page.wait_for_timeout(600)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await page.wait_for_timeout(1000)
                try:
                    await page.wait_for_function(
                        "() => Array.from(document.images).every(img => img.complete)",
                        timeout=3000,
                    )
                except Exception:
                    pass
                await page.evaluate("window.scrollTo(0, 0);")
                await page.wait_for_timeout(500)
            except Exception as scroll_error:
                logger.warning(f"页面滚动失败: {scroll_error}")

            final_source = await page.content()
            if return_status_code and not status_code and main_response:
                status_code = main_response.status

            return final_source, (status_code or 0)
        finally:
            try:
                if page:
                    await page.close()
                if context:
                    await context.close()
                try:
                    await playwright_instance.stop()
                except TypeError:
                    # 兼容某些实现中 stop 为同步方法
                    playwright_instance.stop()
            finally:
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as cleanup_error:
                        logger.warning(f"清理临时目录失败: {cleanup_error}")

    result, status_code = asyncio.run(_run_once())
    if return_status_code:
        return result, status_code
    return result


async def _fetch_one_in_context(
    context,
    url: str,
    return_status_code: bool,
    timeout_ms: int,
    is_mobile: bool
) -> Dict:
    page = await context.new_page()
    try:
        # 与单页路径保持一致的较小默认视口
        try:
            await page.set_viewport_size({'width': 1280, 'height': 800} if not is_mobile else {'width': 375, 'height': 667})
        except Exception:
            pass

        try:
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
        except Exception:
            pass

        await asyncio.sleep(random.uniform(0.3, 1.2))

        status_code = 0
        main_response = None
        try:
            main_response = await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
        except Exception as nav_err:
            logger.warning(f"导航异常: {url}, {nav_err}")

        if return_status_code and main_response:
            status_code = main_response.status

        try:
            await page.wait_for_selector('body', timeout=8000)
        except Exception:
            pass

        # 轻量滚动，触发懒加载
        try:
            page_height = await page.evaluate("document.body.scrollHeight")
            viewport_height = await page.evaluate("window.innerHeight")
            steps = max(2, int(page_height / max(1, viewport_height)))
            for i in range(min(steps, 8)):
                await page.evaluate("(y) => window.scrollTo(0, y)", (i + 1) * viewport_height)
                await page.wait_for_timeout(300)
            await page.evaluate("window.scrollTo(0, 0);")
        except Exception:
            pass

        content = await page.content()
        return {
            'url': url,
            'source_code': content,
            'status_code': status_code if return_status_code else None,
        }
    except Exception as e:
        logger.error(f"抓取失败: {url}, {e}")
        return {
            'url': url,
            'source_code': '',
            'status_code': 0 if return_status_code else None,
        }
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _batch_get_html_sources_async(
    url_list: List[str],
    headless: bool,
    return_status_code: bool,
    **kwargs
) -> List[Dict]:
    max_tabs = int(kwargs.get('max_tabs', 5))
    timeout_ms = kwargs.get('timeout', 60) * 1000
    user_agent = kwargs.get('user_agent')
    is_mobile = kwargs.get('is_mobile', False)
    viewport = kwargs.get('viewport')

    playwright_instance = await async_playwright().start()
    context = None
    temp_dir = None
    try:
        context, temp_dir = await _create_persistent_context_async(
            playwright_instance, headless, user_agent, is_mobile, viewport
        )

        semaphore = asyncio.Semaphore(max_tabs)
        results: List[Dict] = [None] * len(url_list)

        async def worker(idx: int, target: str):
            async with semaphore:
                result = await _fetch_one_in_context(
                    context, target, return_status_code, timeout_ms, is_mobile
                )
                if not return_status_code:
                    result.pop('status_code', None)
                results[idx] = result

        tasks = [asyncio.create_task(worker(i, u)) for i, u in enumerate(url_list)]
        await asyncio.gather(*tasks)
        return results
    finally:
        try:
            if context:
                await context.close()
            try:
                await playwright_instance.stop()
            except TypeError:
                playwright_instance.stop()
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as cleanup_error:
                    logger.warning(f"清理临时目录失败: {cleanup_error}")


def _batch_get_html_sources(
    url_list: List[str],
    headless: bool = True,
    return_status_code: bool = False,
    **kwargs
) -> List[Dict]:
    """批量抓取：单实例多标签页并发（异步驱动，外层同步封装）。"""
    result_path = kwargs.get(
        'result_path', 'results.jsonl' if not return_status_code else 'results_with_status.jsonl'
    )

    results = asyncio.run(_batch_get_html_sources_async(url_list, headless, return_status_code, **kwargs))

    with open(result_path, 'w', encoding='utf-8') as f:
        for item in results:
            json.dump(item, f, ensure_ascii=False)
            f.write('\n')

    logger.info(f"所有任务已完成，结果已按原始顺序保存到 {result_path}")
    return results


if __name__ == '__main__':
    # # 示例：测试单个URL
    # test_url = "http://www.chinattl.com/"
    #
    # print("=== 测试单个URL (Patchright版本) ===")
    # print("\n=== 测试获取源码和状态码 ===")
    # html_source, status_code = get_html_source(test_url, headless=False, return_status_code=True)
    # if html_source:
    #     print(f"成功获取HTML源码，长度: {len(html_source)}，状态码: {status_code}")
    # else:
    #     print("获取HTML源码失败")

    # 示例：测试批量获取（单实例多标签并发）
    with open('urls.txt', 'r', encoding='utf-8') as f:
        url_list = [line.strip() for line in f if line.strip()]

    print("\n=== 测试批量获取（包含状态码，单实例多标签） ===")
    results_with_status = get_html_source(
        url_list,
        headless=False,
        return_status_code=True,
        max_tabs=5,
        timeout=45,
        result_path='patchright_test_results_with_status.jsonl'
    )

    print(f"\n批量处理完成，共处理 {len(results_with_status)} 个URL")
    for result in results_with_status:
        url = result['url']
        content_length = len(result['source_code'])
        status_code = result.get('status_code', 0)
        status = "成功" if content_length > 0 else "失败"
        print(f"URL: {url}")
        print(f"  状态: {status}, 内容长度: {content_length}, HTTP状态码: {status_code}")
