# JAVBus Scrapy 爬虫安装和配置指南

## 📦 安装依赖

### 选项 1：使用 pip 安装
```bash
pip install scrapy requests pillow python-dotenv
```

### 选项 2：更新 pyproject.toml 并使用 uv（推荐）
```bash
# 如果使用 uv 包管理器
uv sync
```

如果需要从 SeleniumBase 迁移，可以卸载它来节省空间：
```bash
pip uninstall seleniumbase
```

## 🔧 配置文件

创建 `.env` 文件在项目根目录：

```env
# JAVBus 网站 URL
JAVBUS_URL=https://www.javbus.com/

# 代理配置
PROXY_ENABLED=false
PROXY=http://proxy.example.com:8080

# SeleniumBase 配置（不再使用，保留以兼容）
HEADLESS=true
```

## 🚀 使用方法

### 基本用法
```bash
python javbus_scrapy.py
```

脚本会提示输入视频目录路径：
```
请输入视频目录路径：D:\Videos
正在扫描文件...
...
```

### 命令行用法（高级）

如果需要直接运行 Scrapy 爬虫：

```bash
# 列出所有可用爬虫
scrapy list

# 运行特定爬虫并导出结果
scrapy crawl javbus -o results.json
```

## 📊 输出说明

脚本执行后会生成以下文件结构：

```
视频目录/
├── CARID Title_1/
│   ├── CARID Title_1.mp4          # 重命名的视频文件
│   ├── CARID Title_1.nfo          # NFO 元数据文件
│   ├── CARID Title_1-fanart.png   # 电影海报（宽）
│   └── CARID Title_1-poster.png   # 电影海报（窄）
├── CARID Title_2/
│   ├── CARID Title_2.mp4
│   ├── CARID Title_2.nfo
│   ├── CARID Title_2-fanart.png
│   └── CARID Title_2-poster.png
└── ...
```

## 🔍 日志输出

脚本会输出详细的处理日志：

```
2024-01-15 10:30:45,123 - INFO - 开始查找车牌...
2024-01-15 10:30:45,456 - INFO - 找到 3 个车牌
2024-01-15 10:30:45,789 - INFO - 车牌：ABF-340, 路径：D:\Videos\HKBISI@ABF-340-C.mp4
2024-01-15 10:30:50,123 - INFO - 页面标题：Censored Title
2024-01-15 10:30:50,456 - INFO - 发行日期：2025-06-23
2024-01-15 10:30:51,789 - INFO - 已下载封面：ABF-340.png
2024-01-15 10:30:52,123 - INFO - 已将 D:\Videos\HKBISI@ABF-340-C.mp4 重命名为 ABF-340 Censored Title.mp4
...
```

## ⚙️ 高级配置

### 自定义 Scrapy 设置

在 `javbus_scrapy.py` 中修改 `JavbusScraperRunner.run()` 方法的 `custom_settings`：

```python
custom_settings = {
    'ROBOTSTXT_OBEY': False,
    'CONCURRENT_REQUESTS': 1,           # 并发请求数
    'DOWNLOAD_DELAY': 2,                # 下载延迟（秒）
    'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
    'COOKIES_ENABLED': True,
    'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'LOG_LEVEL': 'INFO',
    'TIMEOUT': 30,                      # 请求超时
}
```

### 配置代理

如果需要使用代理：

1. 在 `.env` 中配置：
```env
PROXY_ENABLED=true
PROXY=http://proxy.example.com:8080
```

2. 或在代码中直接配置：
```python
custom_settings['PROXY'] = 'http://proxy.example.com:8080'
```

### 配置重试机制

在 `custom_settings` 中添加：

```python
'RETRY_TIMES': 3,                       # 重试次数
'RETRY_HTTP_CODES': [500, 502, 503, 504],
```

## 🐛 故障排除

### 问题 1：导入错误 `No module named 'scrapy'`
**解决方案：**
```bash
pip install scrapy
```

### 问题 2：网站无法访问或响应缓慢
**解决方案：**
- 增加 `DOWNLOAD_DELAY`（延迟）
- 启用代理
- 检查网络连接

### 问题 3：无法下载封面图片
**解决方案：**
- 检查网络连接
- 尝试启用代理
- 检查 JAVBus 网站是否有反爬虫保护

### 问题 4：车牌识别失败
**解决方案：**
- 检查视频文件名是否包含有效的车牌
- 查看 `car.py` 中的车牌识别规则
- 检查 `find_car_bus()` 函数的过滤列表

## 📈 性能优化

### 增加并发（谨慎使用）
```python
'CONCURRENT_REQUESTS': 3,  # 增加到 3-5
```

**警告：** 过高的并发可能导致被服务器封IP。

### 缓存响应（开发时使用）
```python
'HTTPCACHE_ENABLED': True,
'HTTPCACHE_EXPIRATION_SECS': 3600,
```

### 压缩传输
```python
'COMPRESSION_ENABLED': True,
```

## 🔐 安全建议

1. **保护 .env 文件**
   - 不要将 `.env` 文件上传到版本控制系统
   - 在 `.gitignore` 中添加 `.env`

2. **代理认证**
   ```env
   PROXY=http://username:password@proxy.example.com:8080
   ```

3. **速率限制**
   - 增加 `DOWNLOAD_DELAY` 以降低被封IP的风险
   - 使用代理轮换中间件

## 📚 相关文件

- `javbus.py` - 原始的 SeleniumBase 版本
- `javbus_scrapy.py` - Scrapy 重写版本（新）
- `car.py` - 车牌识别模块
- `filesave.py` - 文件保存和 NFO 生成模块
- `utils.py` - 工具函数模块
- `SCRAPY_MIGRATION_GUIDE.md` - 迁移指南

## 🔄 对比其他爬虫方案

| 方案 | 优点 | 缺点 | 适用场景 |
|-----|------|------|--------|
| SeleniumBase | 能处理 JS、反爬虫强 | 耗资源、慢 | 复杂网站、小规模 |
| Scrapy | 轻量、快、可扩展 | 无法处理 JS | 大规模、资源有限 |
| Requests | 简单、轻量 | 功能少、无框架 | 简单爬虫、学习 |

## 💡 下一步建议

1. **添加数据库支持**
   ```bash
   pip install sqlalchemy pymysql
   ```

2. **添加分布式爬虫**
   ```bash
   pip install scrapy-splash
   ```

3. **添加定时任务**
   ```bash
   pip install APScheduler
   ```

4. **添加监控告警**
   ```bash
   pip install prometheus-client
   ```

---

**更新时间：** 2026-05-14  
**版本：** 1.0.0 (Scrapy Edition)
