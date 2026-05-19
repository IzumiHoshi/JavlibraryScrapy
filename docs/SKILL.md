---
name: javlibrary-scraper
description: Use Scrapling-based JAVLibrary scraper for efficient multi-page web scraping with built-in Cloudflare handling. Trigger on requests to "scrape JAVLibrary", "extract video metadata", "crawl mostwanted list", "bypass Cloudflare", or any JAV library data extraction task. Supports proxy rotation, async processing, and exports to JSON/CSV formats.
---

# JAVLibrary Scrapling Scraper

Python-based high-performance web scraper using the Scrapling framework for automated JAVLibrary data extraction. Built for reliable crawling with automatic Cloudflare verification handling, proxy support, and multi-format data export.

Repo: https://github.com/IzumiHoshi/JavlibraryScrapy

## Why use this scraper

| Feature | JAVLibrary Scraper | Raw HTTP |
|---------|-------------------|----------|
| Cloudflare handling | ✅ Automatic | ❌ Manual |
| Dynamic content | ✅ JavaScript support | ❌ HTML only |
| Proxy support | ✅ HTTP/HTTPS/SOCKS5 | ⚠️ Limited |
| Multi-page crawl | ✅ Automatic detection | ❌ Manual |
| Data formats | ✅ JSON/CSV | ⚠️ Manual parsing |
| Async processing | ✅ Native | ⚠️ Requires setup |
| Error recovery | ✅ Built-in retry | ⚠️ Manual |

**Real-world performance on JAVLibrary "Most Wanted" list:**
- Single page: 20-30 seconds (includes Cloudflare bypass)
- All 25 pages: 8-12 minutes (with 3s inter-page delays)
- Data extraction rate: 18-20 videos per page (~450+ per full crawl)

## Installation

```bash
git clone https://github.com/IzumiHoshi/JavlibraryScrapy.git
cd JavlibraryScrapy
uv sync
```

## Quick start

### Test single page
```bash
uv run test_scraper.py
```

### Full crawl
```bash
uv run javlibrary_scrapling.py
```

### Diagnose issues
```bash
uv run test_proxy.py      # Check proxy connectivity
uv run debug_scraper.py   # Trace page loading
uv run verify_parsing.py  # Validate HTML parsing logic
```

## Configuration

**`.env` file:**
```env
PROXY_ENABLED=true
PROXY=http://127.0.0.1:7890
```

Supported proxy formats:
- HTTP: `http://host:port`
- HTTPS: `https://host:port`
- SOCKS5: `socks5://host:port`

## Data extraction

Extracts from each video item:
- **id** — unique identifier (e.g., `javmefjl5q`)
- **code** — video code (e.g., `SNOS-222`)
- **title** — full Japanese title
- **cover_url** — direct image link for thumbnails

**Output formats:**
- `output/javlibrary_movies.json` — JSON array with full metadata
- `output/javlibrary_movies.csv` — CSV with headers for spreadsheet apps

**Example JSON:**
```json
[
  {
    "id": "javmefjl5q",
    "code": "SNOS-222",
    "title": "脚フェチが集うパンストメーカー全男性社員を狂わせた 魔性のあし 楓ふうあ",
    "cover_url": "https://t2.pixhost.to/thumbs/7623/721821470_t677565.jpg"
  }
]
```

## Browser session config

```python
async with AsyncDynamicSession(
    load_dom=True,              # Wait for JS execution
    network_idle=True,          # Wait for network completion
    disable_resources=False,    # Load all resources
    proxy=proxy,                # Route through proxy
    headless=True,              # No UI
    timeout=90000,              # 90s per page
    stealth_mode=True,          # Mask automation signals
) as session:
```

## Scaling profile

- ✅ **High concurrency, moderate resource:** JAVLibrary list pages — 5–10 concurrent fetches per box, clean extraction.
- ✅ **Single-page crawls:** Fast & reliable with proxy support.
- ⚠️ **Large-scale multi-domain:** Works but slower than raw HTTP; use proxy rotation to avoid IP bans.
- ❌ **Interactive login flows:** Scraper handles read-only content; authentication requires manual session injection.

## Known limits

- **Cloudflare protection:** Handled by Scrapling; stealth mode masks browser automation signals.
- **Rate limiting:** Inter-page delays (3s default) required to avoid 403 blocks; increase for stability.
- **IP bans:** Rotate proxy or wait 30 min–several hours for IP cooldown.
- **Dynamic content changes:** HTML structure parsing may need updates if JAVLibrary redesigns.
- **Image URLs:** Proxied from third-party hosts; may expire or face hotlink protection.

## Troubleshooting

### 403 Forbidden errors
- Check proxy IP status with `uv run test_proxy.py`
- Rotate to fresh IP in proxy manager (Clash/V2Ray)
- Increase inter-page delay in `crawl()` method
- See `TROUBLESHOOT_403.md` for detailed diagnosis

### No videos extracted
- Run `uv run verify_parsing.py` against sample HTML
- Check if JAVLibrary HTML structure changed
- Enable debug output in scraper logs
- Save debug HTML: modify `fetch_page()` to dump response

### Proxy not working
- Verify proxy is running and on correct port
- Test with `uv run test_proxy.py`
- Try different proxy type (SOCKS5 vs HTTP)
- Check `.env` file for typos

## Code structure

**Main components:**
- `JAVLibrarySpider` — orchestrates crawling
  - `fetch_page()` — HTTP request with headers
  - `parse_movies_from_html()` — extract video data via CSS selectors
  - `get_page_count()` — auto-detect pagination
  - `crawl()` — main loop with rate-limiting
  - `save_to_json()` / `save_to_csv()` — export handlers

**Helper scripts:**
- `test_scraper.py` — minimal one-page test
- `debug_scraper.py` — detailed diagnostics
- `test_proxy.py` — proxy validation
- `verify_parsing.py` — HTML structure validation

## Performance tips

1. **Reduce latency:**
   - Use fast proxy (nearby data center)
   - Parallel execution for independent pages (requires code changes)

2. **Avoid bans:**
   - Keep inter-page delay ≥ 3 seconds
   - Rotate proxy IPs every 10–20 pages
   - Use stealth mode against bot detection

3. **Extract efficiently:**
   - Stream results to DB instead of buffering all in memory
   - Implement checkpoint/resume for long crawls
   - Batch CSV writes every 50 videos

## Integration examples

**Import into custom script:**
```python
import asyncio
from pathlib import Path
from javlibrary_scrapling import JAVLibrarySpider

async def main():
    spider = JAVLibrarySpider(
        output_dir=Path("./my_output"),
        proxy="http://127.0.0.1:7890"
    )
    await spider.crawl(max_pages=5)
    spider.save_to_json("batch_results.json")

asyncio.run(main())
```

**Extend with custom export:**
```python
def save_to_database(self, db_connection):
    for movie in self.movies:
        db_connection.insert("videos", movie)
```

## Documentation

- **Quick start:** `QUICKSTART.md`
- **Detailed guide:** `HOWTO.md`
- **Troubleshooting:** `TROUBLESHOOT_403.md`
- **Full reference:** `JAVLIBRARY_SCRAPER_GUIDE.md`

## Dependencies

- **Scrapling** — dynamic browser rendering with CDP
- **Playwright** — browser automation backend
- **aiohttp** — async HTTP client
- **python-dotenv** — environment config
- **Pillow** — image processing (optional)

## Safety & ethics

- Respects website robots.txt; rate-limits requests
- No authentication bypass or credential theft
- Extracts publicly visible metadata only
- Use proxy to distribute request load
- Comply with terms of service of target site

## License

Educational use. See repository LICENSE for details.

## Support

- **FAQ:** See `HOWTO.md` section "Common Questions"
- **Diagnosis:** Run diagnostic scripts: `test_proxy.py`, `debug_scraper.py`
- **Issues:** Check repository for known problems & workarounds
