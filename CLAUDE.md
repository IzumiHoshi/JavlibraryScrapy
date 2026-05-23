# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python web scraper for extracting adult video metadata from JAVBus and JAVLibrary, generating Kodi/Plex-compatible NFO files and organizing video collections.

## Common Commands

```bash
# Install dependencies
uv sync

# Run JAVBus scraper (prompts for video directory, generates NFO files)
uv run javbus_scrapling.py

# Run JAVLibrary scraper (crawls "Most Wanted" list, outputs JSON/CSV)
uv run javlibrary_scrapling.py

# Complete workflow: scan → scrape → output NFO/covers
uv run python scripts/workflow.py <download_path> <output_path> [--preview]

# Run tests
uv run pytest
```

## Directory Structure

```
├── javbus_scrapling.py       # JAVBus spider (main entry point)
├── javlibrary_scrapling.py  # JAVLibrary spider
├── utils/
│   ├── car.py               # Car code extraction via regex
│   ├── filesave.py          # NFO XML generation, file rename
│   └── fanart.py            # Poster extraction (5:7 crop)
├── scripts/
│   ├── workflow.py           # End-to-end workflow
│   ├── move_videos.py       # Move videos with size filter
│   └── rename_at_symbol.py  # Strip @ prefix from filenames
├── test/                     # Test scripts
├── deprecated/               # Obsolete versions (Selenium/Scrapy)
└── docs/archive/            # Archived development docs
```

## Architecture

### Spiders

- **javbus_scrapling.py** - `JavbusSpider`: Scans video filenames for car codes, fetches JAVBus metadata, generates NFO files
- **javlibrary_scrapling.py** - `JAVLibrarySpider`: Crawls JAVLibrary "Most Wanted" pages, outputs JSON/CSV

Both use `scrapling.fetchers.AsyncDynamicSession` for JavaScript rendering and Cloudflare bypass.

### Utils

- **utils/car.py** - `javbuscar()`: Scans directory for video files, extracts car codes via regex patterns (handles T28, ID-format, standard)
- **utils/filesave.py** - `write_xml()`: Generates Kodi-compatible NFO XML; `rename()`: Moves/renames files
- **utils/fanart.py** - `split_poster_from_fanart()`: Extracts 5:7 poster from fanart (right half crop)

### Scripts

- **scripts/workflow.py**: End-to-end workflow (scan videos → scrape JAVBus → output NFO/covers to target dir)
- **scripts/move_videos.py**: Move videos with size filter (robocopy for large files ≥100MB)
- **scripts/rename_at_symbol.py**: Strip `@prefix` from filenames

## Configuration

Environment variables in `.env`:
- `JAVBUS_URL` - JAVBus base URL
- `PROXY_ENABLED` / `PROXY` - Proxy settings (required in many regions)
- `SCRAPLING_*` - Scrapling session options (load_dom, network_idle, timeout)
- `USER_AGENT`, `DOWNLOAD_TIMEOUT`, `VERIFY_SSL` - Download settings

## Key Technical Notes

- Uses `AsyncDynamicSession` to handle JavaScript-rendered pages and Cloudflare bot verification
- Cover image downloads require proper `Referer` header pointing to video page (403 otherwise)
- Car code regex in `find_car_bus()` handles: T28, ID-format (20ID-020), standard (ABC-123), and exclusion list for certain series
- NFO files use fixed XML schema: NC-17 rating, JP country, hardcoded studio/director mapping