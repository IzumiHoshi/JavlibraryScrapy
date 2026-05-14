# JAVBus Web Scraper (Rewritten with Scrapling)

A Python web scraper for extracting adult video metadata from JAVBus and generating NFO metadata files compatible with media center software like Kodi/Plex.

**Status**: ✨ Recently rewritten using [Scrapling](https://github.com/D4Vinci/Scrapling) for improved reliability and performance.

## Features

- **Dynamic Content Handling**: Uses Scrapling's `AsyncDynamicSession` for proper JavaScript rendering
- **Metadata Extraction**: Extracts title, release date, producer, publisher, categories, and actors
- **Image Download**: Automatically downloads and processes cover art with proper HTTP headers
- **NFO Generation**: Creates Kodi-compatible XML metadata files
- **Poster Processing**: Automatically generates poster images from cover art
- **Proxy Support**: Built-in support for HTTP/HTTPS proxies
- **Error Handling**: Comprehensive logging and error recovery

## Requirements

- **Python** 3.9+
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **Proxy** (required for JAVBus access from many regions)
- **Windows/macOS/Linux** environment

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/IzumiHoshi/JavlibraryScrapy.git
cd JavlibraryScrapy
```

### 2. Install Dependencies with uv

```bash
uv sync
```

This will install all dependencies including:
- `scrapling` - Web scraping framework
- `python-dotenv` - Environment variable management
- `lxml` - XML processing
- `Pillow` - Image processing

### 3. Initialize Scrapling (First Time Only)

After installing dependencies, initialize Scrapling's browser environment:

```bash
uv run python -c "from scrapling.fetchers import DynamicFetcher; print('Scrapling initialized')"
```

This command will:
1. Download and install the Chromium browser engine (if not already installed)
2. Set up the necessary browser profiles and cache
3. Verify the installation is complete

**Note**: First-time setup may take a few minutes as it downloads the browser (~300MB).

If you have Google Chrome already installed and want to use it instead of Chromium:

```bash
uv run playwright install chrome
```

Then set `real_chrome=True` in the spider configuration (if needed).

### 4. Configure Environment

Create a `.env` file in the root directory:

```env
# JAVBus configuration
JAVBUS_URL=https://www.javbus.com/

# Proxy settings (optional but recommended)
PROXY_ENABLED=True
PROXY=http://127.0.0.1:10808
```

## Usage

### Basic Usage

```bash
uv run javbus_scrapling.py
```

When prompted, enter the path to your videos directory:
```
请输入视频目录路径：C:\Videos\MyCollection
```

The script will:
1. Find all video files and extract video codes (e.g., ABF-340)
2. Fetch metadata from JAVBus for each video
3. Download cover art (with proper headers to bypass hotlink protection)
4. Generate Kodi-compatible NFO files
5. Create poster images from cover art
6. Organize files in subdirectories with metadata

### Output Structure

```
C:\Videos\MyCollection\
├── ABF-340 性欲に支配された倒錯カップルの同棲中出し性交録。 瀧本雫葉/
│   ├── ABF-340 性欲に...mp4 (original video)
│   ├── ABF-340 性欲に...nfo (Kodi metadata)
│   ├── ABF-340 性欲に...-fanart.png (cover art)
│   └── ABF-340 性欲に...-poster.png (poster thumbnail)
```

### Generated NFO File Example

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

## Architecture

### Core Components

1. **JavbusSpider Class**
   - Manages the scraping session using Scrapling's `AsyncDynamicSession`
   - Handles async operations for multiple videos
   - Manages proxy and browser configuration

2. **parse() Method**
   - Extracts metadata from JAVBus HTML pages
   - Handles both absolute and relative image URLs
   - Saves debug HTML for troubleshooting

3. **download_cover() Method**
   - Asynchronously downloads cover images
   - Includes proper HTTP headers (User-Agent, Referer)
   - Supports proxy connection

4. **process_movie() Method**
   - Renames video files with proper metadata
   - Generates NFO files
   - Creates poster images

## Technical Details

### Browser Configuration

```python
async with AsyncDynamicSession(
    load_dom=True,              # Wait for JavaScript to load
    network_idle=True,          # Wait for network idle
    disable_resources=True,     # Skip non-essential resources (25% faster)
    proxy=self.proxy,           # Use configured proxy
    headless=True,              # Run in headless mode
    timeout=30000,              # 30 second timeout
) as session:
```

### Header Configuration for Image Download

```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...',
    'Referer': f'{self.javbus_url}{car_id}',  # Critical for hotlink bypass
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}
```

## Troubleshooting

### 403 Forbidden Error on Image Download
- **Cause**: Missing or incorrect Referer header
- **Solution**: Ensure the Referer header points to the video page

### JavaScript Not Loading
- **Cause**: Browser timeout or network issues
- **Solution**: Increase `timeout` parameter in AsyncDynamicSession

### Metadata Not Extracted
- **Solution**: Check the debug HTML file (`{video_code}_debug.html`) saved in your video directory

### Proxy Connection Issues
- **Ensure**: Proxy is running and accessible at the configured address
- **Check**: PROXY_ENABLED is set to `True` in .env

## Development

### Debugging

Enable debug HTML output to inspect page structure:
```python
debug_file = self.root_dir / f"{car_id}_debug.html"
# HTML is automatically saved for each video
```

### Testing Single Video

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

## Related Projects

- **Previous Version**: [Original JavlibraryScrapy](https://github.com/desonglll/JavlibraryScrapy)
- **Scraping Framework**: [Scrapling](https://github.com/D4Vinci/Scrapling)
- **Docs**: [Scrapling Documentation](https://scrapling.readthedocs.io/)

## License

This project is provided as-is for educational purposes.

## Notes

- This tool requires a proxy to access JAVBus from most regions
- Respect the website's terms of service and robots.txt
- The spider uses anti-detection measures (real User-Agents, proper headers, controlled request rates)
- Generated metadata is compatible with Kodi, Plex, and similar media center software
