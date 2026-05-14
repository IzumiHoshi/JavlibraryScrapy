# JAVBus Web 爬虫（使用 Scrapling 重写）

一个 Python Web 爬虫，用于从 JAVBus 提取成人视频元数据，并生成与 Kodi/Plex 等媒体中心软件兼容的 NFO 元数据文件。

**状态**: ✨ 最近使用 [Scrapling](https://github.com/D4Vinci/Scrapling) 重写，提高了可靠性和性能。

## 功能特性

- **动态内容处理**: 使用 Scrapling 的 `AsyncDynamicSession` 进行适当的 JavaScript 渲染
- **元数据提取**: 提取标题、发行日期、制作商、发行商、分类和演员信息
- **图片下载**: 自动下载和处理封面艺术，使用正确的 HTTP 标头
- **NFO 生成**: 创建 Kodi 兼容的 XML 元数据文件
- **海报处理**: 自动从封面生成海报图像
- **代理支持**: 内置 HTTP/HTTPS 代理支持
- **错误处理**: 全面的日志记录和错误恢复

## 需求

- **Python** 3.9+
- **[uv](https://docs.astral.sh/uv/)** (Python 包管理器)
- **代理** (从大多数地区访问 JAVBus 需要)
- **Windows/macOS/Linux** 环境

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/IzumiHoshi/JavlibraryScrapy.git
cd JavlibraryScrapy
```

### 2. 使用 uv 安装依赖

```bash
uv sync
```

这将安装所有依赖，包括：
- `scrapling` - Web 爬虫框架
- `python-dotenv` - 环境变量管理
- `lxml` - XML 处理
- `Pillow` - 图像处理

### 3. 初始化 Scrapling（仅首次需要）

安装依赖后，初始化 Scrapling 的浏览器环境：

```bash
uv run python -c "from scrapling.fetchers import DynamicFetcher; print('Scrapling initialized')"
```

此命令将：
1. 下载并安装 Chromium 浏览器引擎（如果尚未安装）
2. 设置必要的浏览器配置文件和缓存
3. 验证安装完成

**注意**: 首次设置可能需要几分钟，因为它会下载浏览器（~300MB）。

如果您已安装 Google Chrome 并想使用它而不是 Chromium：

```bash
uv run playwright install chrome
```

然后在爬虫配置中设置 `real_chrome=True`（如果需要）。

### 4. 配置环境

在根目录创建 `.env` 文件：

```env
# JAVBus 配置
JAVBUS_URL=https://www.javbus.com/

# 代理设置（可选但推荐）
PROXY_ENABLED=True
PROXY=http://127.0.0.1:10808
```

## 使用方法

### 基本使用

```bash
uv run javbus_scrapling.py
```

当提示时，输入您的视频目录路径：
```
请输入视频目录路径：C:\Videos\MyCollection
```

脚本将：
1. 查找所有视频文件并提取视频代码（例如 ABF-340）
2. 从 JAVBus 获取每个视频的元数据
3. 下载封面艺术（使用适当的标头绕过防盗链保护）
4. 生成 Kodi 兼容的 NFO 文件
5. 从封面创建海报图像
6. 使用元数据将文件组织在子目录中

### 输出结构

```
C:\Videos\MyCollection\
├── ABF-340 性欲に支配された倒錯カップルの同棲中出し性交録。 瀧本雫葉/
│   ├── ABF-340 性欲に...mp4 (原始视频文件)
│   ├── ABF-340 性欲に...nfo (Kodi 元数据)
│   ├── ABF-340 性欲に...-fanart.png (封面艺术)
│   └── ABF-340 性欲に...-poster.png (海报缩略图)
```

### 生成的 NFO 文件示例

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>性欲に支配された倒錯カップルの同棲中出し性交録。</title>
  <id>ABF-340</id>
  <director>プレステージ</director>
  <studio>プレステージ</studio>
  <premiered>2026-04-17</premiered>
  <genre>フルハイビジョン(FHD)</genre>
  <genre>巨乳</genre>
  <actor>
    <name>瀧本雫葉</name>
  </actor>
</movie>
```

## 架构

### 核心组件

1. **JavbusSpider 类**
   - 使用 Scrapling 的 `AsyncDynamicSession` 管理爬虫会话
   - 处理多个视频的异步操作
   - 管理代理和浏览器配置

2. **parse() 方法**
   - 从 JAVBus HTML 页面提取元数据
   - 处理绝对和相对图像 URL
   - 保存调试 HTML 以便故障排除

3. **download_cover() 方法**
   - 异步下载封面图像
   - 包含正确的 HTTP 标头（User-Agent、Referer）
   - 支持代理连接

4. **process_movie() 方法**
   - 用适当的元数据重命名视频文件
   - 生成 NFO 文件
   - 创建海报图像

## 技术细节

### 浏览器配置

```python
async with AsyncDynamicSession(
    load_dom=True,              # 等待 JavaScript 加载
    network_idle=True,          # 等待网络空闲
    disable_resources=True,     # 跳过非必需资源（快 25%）
    proxy=self.proxy,           # 使用配置的代理
    headless=True,              # 在无界面模式运行
    timeout=30000,              # 30 秒超时
) as session:
```

### 图像下载标头配置

```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...',
    'Referer': f'{self.javbus_url}{car_id}',  # 对防盗链绕过至关重要
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}
```

## 故障排除

### 图像下载时出现 403 Forbidden 错误
- **原因**: 缺少或不正确的 Referer 标头
- **解决方案**: 确保 Referer 标头指向视频页面

### JavaScript 未加载
- **原因**: 浏览器超时或网络问题
- **解决方案**: 增加 AsyncDynamicSession 中的 `timeout` 参数

### 未提取元数据
- **解决方案**: 检查保存在视频目录中的调试 HTML 文件（`{video_code}_debug.html`）

### 代理连接问题
- **确保**: 代理正在运行且可在配置的地址访问
- **检查**: .env 中的 PROXY_ENABLED 设置为 `True`

## 开发

### 调试

为每个视频启用调试 HTML 输出以检查页面结构：
```python
debug_file = self.root_dir / f"{car_id}_debug.html"
# HTML 会自动保存
```

### 测试单个视频

```python
from javbus_scrapling import JavbusSpider
from pathlib import Path
import asyncio

async def test():
    spider = JavbusSpider(root_dir=Path("C:\\Videos"))
    cars = [("ABF-340", "C:\\Videos\\ABF-340.mp4")]
    await spider.crawl_and_process(cars)

asyncio.run(test())
```

## 相关项目

- **前一版本**: [原始 JavlibraryScrapy](https://github.com/desonglll/JavlibraryScrapy)
- **爬虫框架**: [Scrapling](https://github.com/D4Vinci/Scrapling)
- **文档**: [Scrapling 文档](https://scrapling.readthedocs.io/)

## 许可证

本项目仅供教育目的使用。

## 注意事项

- 此工具需要代理才能从大多数地区访问 JAVBus
- 尊重网站的服务条款和 robots.txt
- 爬虫使用反检测措施（真实的 User-Agents、正确的标头、受控的请求速率）
- 生成的元数据与 Kodi、Plex 和类似的媒体中心软件兼容
