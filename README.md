# 🕷️ Website Spider

一个强大的通用网页源码获取工具，专为复杂网站环境设计。

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## ✨ 特性

- 🚀 **高性能抓取** - 基于 Playwright 的现代化网页抓取
- 🛡️ **强反检测** - 内置多层反检测机制，专门针对政府网站等严格反爬场景
- 🔄 **智能重试** - 多策略自动重试，支持无头/有头模式自动切换
- 📱 **多设备支持** - 支持桌面端和移动端 User-Agent 模拟
- 🎯 **高成功率** - 针对复杂网站优化的加载策略
- ⚡ **简单易用** - 一行代码即可获取完整渲染后的HTML源码

## 🚀 快速开始

### 安装

推荐使用 [uv](https://github.com/astral-sh/uv) 进行项目管理：

```bash
# 安装 uv (如果还没有安装)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆项目
git clone https://github.com/yourusername/website-spider.git
cd website-spider

# 安装依赖
uv install

# 安装 Playwright 浏览器
uv run playwright install chromium
```

### 基础使用

```python
from website_spider.playwright_use import get_html_source

# 获取网页源码
url = "https://example.com"
html_content = get_html_source(url)

if html_content:
    print(f"成功获取HTML，长度: {len(html_content)}")
    # 处理你的HTML内容
else:
    print("获取失败")
```

## 📚 详细用法

### 基本参数

```python
# 优先使用无头模式（默认）
html = get_html_source("https://example.com", headless=True)

# 优先使用有头模式
html = get_html_source("https://example.com", headless=False)
```

### 高级特性

该工具内置了多种智能策略：

1. **自动模式切换** - 无头模式失败时自动切换到有头模式
2. **多User-Agent策略** - 自动尝试桌面端和移动端 User-Agent
3. **智能等待** - 根据页面加载情况动态调整等待时间
4. **反检测机制** - 内置多层反检测脚本，绕过常见的爬虫检测

### 支持的复杂场景

- ✅ 政府网站
- ✅ 银行等金融网站
- ✅ 有复杂反爬机制的网站
- ✅ 需要JavaScript渲染的SPA应用
- ✅ 有懒加载内容的页面

## 🛠️ 开发

### 环境要求

- Python 3.12+
- uv 包管理器

### 开发安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/website-spider.git
cd website-spider

# 安装开发依赖
uv install --dev

# 安装浏览器
uv run playwright install
```

### 运行测试

```bash
# 运行示例
uv run python website_spider/playwright-use.py
```

## 🔧 技术实现

### 架构设计

```
website-spider/
├── website_spider/           # 核心模块
│   └── playwright-use.py    # Playwright实现
├── pyproject.toml           # 项目配置
└── README.md               # 文档
```

### 核心技术

- **Playwright** - 现代化浏览器自动化框架
- **反检测技术** - 多层反检测脚本
- **智能重试** - 多策略容错机制
- **User-Agent轮换** - 桌面端/移动端模拟

### 反检测机制

- WebDriver属性隐藏
- Canvas指纹随机化
- WebGL参数伪造
- Audio Context指纹干扰
- Navigator对象完整模拟
- Performance API随机化

## 📋 待办事项

- [ ] Selenium 实现支持
- [ ] 代理支持
- [ ] 并发抓取
- [ ] 结果缓存
- [ ] 配置文件支持
- [ ] CLI工具
- [ ] Docker支持
- [ ] 更多浏览器支持

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 贡献指南

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [Playwright](https://playwright.dev/) - 强大的浏览器自动化框架
- [uv](https://github.com/astral-sh/uv) - 快速的Python包管理器
- [Loguru](https://github.com/Delgan/loguru) - 优雅的日志库

## 📞 联系方式

如果你有任何问题或建议，请通过以下方式联系：

- 提交 [Issue](https://github.com/yourusername/website-spider/issues)
- 发送邮件至 [your.email@example.com](mailto:your.email@example.com)

---

⭐ 如果这个项目对你有帮助，请给它一个 Star！
