# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python web scraper for extracting adult video metadata from **JAVBus** (per-video metadata → Kodi/Plex NFO) and **JAVLibrary** (multi-page "Most Wanted" list → JSON/CSV). Built on the [Scrapling](https://github.com/D4Vinci/Scrapling) framework for JavaScript rendering and Cloudflare bypass. Project name in `pyproject.toml` is `javlibraryscrapy`, Python 3.11, managed with `uv`.

## Common Commands

```bash
# Install/sync dependencies
uv sync

# JAVBus: scan a video directory, scrape metadata, generate NFO + covers
uv run javbus_scrapling.py
# (prompts for video directory)

# JAVLibrary: crawl "Most Wanted" list, output JSON/CSV
uv run javlibrary_scrapling.py

# End-to-end workflow: move large videos → strip "@" prefix → scrape
uv run python workflow.py <download_path> <intermediate_path> <output_path> [--min-size 500] [--preview]

# Move videos with size filter (uses robocopy on Windows for files ≥100MB)
uv run python scripts/move_videos.py <source> <destination> [--min-size 500]

# Strip "@site.com@" prefix from filenames
uv run python scripts/rename_at_symbol.py <path> [--preview]

# Debug helpers (manual scripts, not pytest)
uv run python test/test_proxy.py          # verify proxy + Referer header reach JAVLibrary
uv run python test/debug_scraper.py       # diagnose AsyncDynamicSession loading
uv run python test/test_scraper.py        # crawl first page of JAVLibrary only
uv run python test/verify_parsing.py      # parse a saved HTML file from temp/
```

> The `test/` directory contains manual debug scripts — there is no `pytest` suite.

## Architecture

### Spiders (top-level entry points)

- **`javbus_scrapling.py`** — `JavbusSpider`: scans a video directory, extracts car codes, fetches JAVBus per-video pages via `AsyncDynamicSession`, downloads covers, generates NFO + poster/fanart in `<root>/<CARID> <title>/`.
  - `parse()` extracts title, release date, producer/publisher, genres, actors, cover URL, and magnet link (with HD+subtitle > HD > standard priority in `_extract_magnet_link`).
  - `download_cover()` uses synchronous `requests` (not the Scrapling session) so the `Referer` header pointing at the video page can be set explicitly — required to avoid 403.
  - `process_movie()` is subclassed by `workflow.py` to redirect output to a different directory.

- **`javlibrary_scrapling.py`** — `JAVLibrarySpider`: crawls JAVLibrary `vl_mostwanted.php` (or a configurable base URL), auto-detects total page count, sleeps 3s between pages, exports `movies.json` + `movies.csv` to `output/`. Uses `stealth_mode=True` and 90s timeout to clear Cloudflare.

Both spiders use `scrapling.fetchers.AsyncDynamicSession` for JS rendering.

### `utils/`

- **`car.py`** — `find_car_bus(file, list_suren_car)` extracts a JAVBus car code from an uppercased filename. Three regex branches in priority order: `T28-###`, `##ID-###` (e.g. `20ID-020`), then the standard `[A-Z]+-###`. Trims leading zeros from long suffixes (`AVOP00127` → `AVOP-127`). Hardcoded exclusion list: `LUXU`, `MIUM`, `HEYZO`, `PONDO`, `CARIB`, `OKYOHOT` (no JAVBus page exists for these).
- **`filesave.py`** — `write_xml(nfo_path, info)` emits Kodi/Plex NFO with hardcoded `mpaa=NC-17`, `countrycode=JP`, `country=日本`; splits categories/actors on spaces; escapes XML. `rename()` is a safe `Path.rename` wrapper that no-ops if the destination exists.
- **`fanart.py`** — `split_poster_from_fanart(fanart, poster)` crops the **right edge** of the fanart to a 5:7 ratio (this is how JAVBus lays out poster-over-fanart). Note: `process_all_fanarts()` is dead code — the `poster_path` assignment is inside the loop body in the wrong scope.

### `scripts/`

- **`move_videos.py`** — recursive video file copy with `--min-size` filter (default 500 MB). Files ≥100 MB go through `robocopy` (Windows) for progress; smaller files use `shutil.move`. Interactive prompt on filename conflicts (overwrite / skip / rename).
- **`rename_at_symbol.py`** — strips everything before the first `@` in filenames (`hkbisi.com@ABF-340-C.mp4` → `ABF-340-C.mp4`). Supports `--preview`.

> `scripts/README.md` references a `scripts/workflow.py` that does not exist — the workflow lives at the **project root** as `workflow.py`.

### `workflow.py` (root, the canonical end-to-end pipeline)

Three steps:
1. `step1_move_videos()` — `shutil.move` (no robocopy) all files ≥`--min-size` MB from `download_path` → `intermediate_path`.
2. `step2_clean_at_prefix()` — removes `@`-prefix from filenames in `intermediate_path` (or just logs in `--preview`).
3. `step3_scrape()` — subclasses `JavbusSpider` to override `process_movie()`: each car gets its own subdirectory under `output_path` named `<CARID> <title>`, the video is moved in, NFO is written, and the cover is copied to `fanart.png` then split to `poster.png`.

`--preview` runs only steps 1–2 and stops before scraping.

## Configuration (`.env`)

```env
JAVBUS_URL=https://www.javbus.com/        # used as URL prefix for car pages
JAVBUS_BASE_URL=https://www.javbus.com    # used to resolve relative cover URLs
PROXY_ENABLED=false                       # required from most regions
PROXY=http://127.0.0.1:10808              # HTTP/HTTPS/SOCKS5
SCRAPLING_LOAD_DOM=true
SCRAPLING_NETWORK_IDLE=true
SCRAPLING_DISABLE_RESOURCES=true
SCRAPLING_HEADLESS=true
SCRAPLING_TIMEOUT=30000                   # ms; JAVLibrary uses 90s internally
USER_AGENT=Mozilla/5.0 (...)
DOWNLOAD_TIMEOUT=10
VERIFY_SSL=false
```

JAVLibrary reads `PROXY_ENABLED`/`PROXY` directly in `main()` and ignores the Scrapling-prefixed variables.

## Key Technical Notes

- **Cloudflare bypass**: JAVBus uses `AsyncDynamicSession` with `disable_resources=True` (≈25% faster) and 30s timeout. JAVLibrary uses `stealth_mode=True`, `disable_resources=False`, 90s timeout.
- **Cover 403 fix**: cover image download must set `Referer: <JAVBUS_URL><car_id>` — the JAVBus image CDN rejects requests without it.
- **Magnet priority**: `HD + 字幕` > `HD` > `标准`. A hit on the top tier short-circuits the loop.
- **Filename encoding**: all I/O uses UTF-8; car regex expects an uppercased filename.
- **Output layout** (per video): `<CARID> <title>/` containing `<prefix>.<ext>` (video), `<prefix>.nfo`, `fanart.png`, `poster.png`. The root-level `JavbusSpider.process_movie()` puts the cover at `fanart.png`; `workflow.py`'s subclass does the same.
- **No real test suite**: `test/` scripts hit the network and rely on `temp/*.html` fixtures. The `.pytest_cache/` is stale.

## Out of Scope / Deprecated

- `deprecated/javbus.py`, `deprecated/javbus_scrapy.py` — older Selenium/Scrapy implementations, kept for reference only.
- `docs/archive/` — historical dev docs (Scrapling migration, troubleshoot 403, etc.). See `docs/archive/SKILL.md` for an archived JAVLibrary-scraper skill description.
