# JAVBus 爬虫 - Scrapy 重写版本对比

## 📋 功能对比

### 原始版本 (javbus.py)
- ✓ 基于 SeleniumBase（浏览器自动化）
- ✓ 通过截图绕过反爬虫
- ✓ 支持代理和无头浏览器模式
- ✓ 获取 JAVBus 页面信息（标题、导演、演员等）
- ✓ 下载封面图片并分离 poster
- ✓ 生成 NFO 元数据文件

### Scrapy 重写版本 (javbus_scrapy.py)
- ✓ 基于 Scrapy 框架（轻量级爬虫框架）
- ✓ 支持并发请求（虽然设置为1以避免被封IP）
- ✓ 自动下载延迟和 User-Agent 轮换
- ✓ 同样获取所有页面信息
- ✓ 使用 requests 库下载图片（更快）
- ✓ 同样支持代理配置
- ✓ 生成相同的 NFO 元数据文件

## 🔄 架构对比

### SeleniumBase 方案
```
┌─────────────────┐
│  SeleniumBase   │  (浏览器自动化)
│  浏览器驱动      │
│  Headless/GUI   │
└────────┬────────┘
         │
    ┌────▼─────┐
    │ JAVBus   │
    │ 网站     │
    └─────────┘
```

**优点：**
- 处理 JavaScript 渲染的内容
- 可以绕过复杂的反爬虫机制
- 支持截图保存（绕过图片防盗链）

**缺点：**
- 内存占用高（浏览器进程）
- 速度慢（需要完全加载页面）
- 资源消耗大

### Scrapy 方案
```
┌──────────────────┐
│  Scrapy 框架     │  (轻量级爬虫)
│  - 管道          │
│  - 中间件        │
│  - 下载器        │
└────────┬─────────┘
         │
    ┌────▼─────┐
    │ JAVBus   │
    │ 网站     │
    └─────────┘
```

**优点：**
- 内存占用低
- 速度快（纯 HTTP 请求）
- 支持并发
- 可扩展性强
- 内置中间件支持（代理、User-Agent 等）

**缺点：**
- 无法处理动态 JavaScript 内容
- 可能无法绕过某些反爬虫机制

## 🚀 使用方法

### 原始版本
```bash
python javbus.py
```

### Scrapy 版本
```bash
python javbus_scrapy.py
```

## 📦 依赖对比

### 原始版本依赖
```
seleniumbase
selenium
dotenv
Pillow (PIL)
requests
```

### Scrapy 版本依赖
```
scrapy
dotenv
Pillow (PIL)
requests
```

**注意：** Scrapy 版本移除了对 SeleniumBase 的依赖，只需要 Scrapy。

## 🔧 配置

两个版本都支持通过 `.env` 文件配置：

```env
JAVBUS_URL=https://www.javbus.com/
PROXY_ENABLED=false
PROXY=http://proxy.example.com:8080
HEADLESS=true
```

## 📊 性能对比

| 指标 | SeleniumBase | Scrapy |
|-----|-------------|--------|
| 单页面爬取时间 | 5-10秒 | 1-2秒 |
| 内存占用 | 200-500MB | 50-100MB |
| CPU 占用 | 高 | 低 |
| 反爬虫绕过能力 | 强 | 中等 |
| 并发能力 | 低 | 高 |

## 🔗 核心方法对应

| 功能 | SeleniumBase | Scrapy |
|-----|-------------|--------|
| 开启页面 | `self.open(url)` | `scrapy.Request()` |
| 获取文本 | `self.get_text(selector)` | `response.css()` |
| 下载图片 | `self.save_element_as_image_file()` | `requests.get()` |
| 并行处理 | 串行 | 可并行 |
| 错误处理 | try/except | `errback` 回调 |

## 💡 何时使用哪个版本

### 使用 SeleniumBase 版本
- ✓ 网站有复杂的 JavaScript 渲染
- ✓ 需要处理登录、验证码等
- ✓ 需要实时截图或其他浏览器操作
- ✓ 反爬虫机制非常强

### 使用 Scrapy 版本
- ✓ 静态 HTML 内容爬取
- ✓ 需要高性能和低资源消耗
- ✓ 需要批量爬取大量页面
- ✓ 硬件资源有限（服务器环境）
- ✓ 需要分布式爬虫能力

## 📝 扩展说明

### Scrapy 版本的优势扩展方向

1. **数据库存储**
   ```python
   # 可以轻松添加数据库管道
   class MoviePipeline:
       def process_item(self, item, spider):
           # 存储到数据库
           return item
   ```

2. **分布式爬取**
   ```python
   # Scrapy 原生支持分布式
   # 可使用 Scrapy-Splash 等中间件
   ```

3. **定时任务**
   ```python
   # 可集成 APScheduler 进行定时更新
   ```

4. **监控和日志**
   ```python
   # Scrapy 内置详细的统计信息
   # 可导出为 JSON、CSV 等格式
   ```

## 🔄 迁移指南

如果要从 SeleniumBase 版本迁移到 Scrapy 版本：

1. **安装 Scrapy**
   ```bash
   pip install scrapy
   ```

2. **卸载 SeleniumBase（可选）**
   ```bash
   pip uninstall seleniumbase
   ```

3. **使用新的爬虫文件**
   ```bash
   python javbus_scrapy.py
   ```

4. **验证输出**
   - 检查 NFO 文件内容是否完整
   - 检查封面图片是否正确下载
   - 检查日志输出是否正常

## ⚙️ 高级配置

### 自定义中间件（Scrapy）
```python
# 可以添加自定义中间件处理特殊情况
class CustomMiddleware(scrapy.middleware.DownloaderMiddleware):
    def process_request(self, request, spider):
        # 修改请求
        return None
```

### 自定义管道（Scrapy）
```python
# 可以添加数据处理管道
class CustomPipeline:
    def process_item(self, item, spider):
        # 处理数据
        return item
```

## 📚 参考资源

- [Scrapy 官方文档](https://docs.scrapy.org/)
- [SeleniumBase 官方文档](https://seleniumbase.io/)
- [Requests 库文档](https://requests.readthedocs.io/)

---

**总结：** Scrapy 版本提供了更高效、更轻量级的爬虫解决方案，特别适合在资源受限的环境中运行或需要处理大量页面时使用。
