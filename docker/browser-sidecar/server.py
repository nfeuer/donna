"""Playwright browser sidecar service.

Provides HTTP endpoints for headless browser rendering, text extraction,
and screenshots. Results are persisted to a shared volume for gallery viewing.

Endpoints:
    POST /extract-text  — Navigate to URL, extract text via CSS selector.
    POST /screenshot    — Navigate to URL, capture full-page screenshot.
    GET  /gallery       — HTML gallery of saved screenshots and extractions.
    GET  /screenshots/* — Static file serving for screenshot assets.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from aiohttp import web
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("BROWSER_DATA_DIR", "/data/browser"))
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
PORT = int(os.environ.get("BROWSER_PORT", "3100"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _error_response(
    message: str,
    error_type: str,
    duration_ms: float,
    status: int = 500,
) -> web.Response:
    body = {
        "error": message,
        "error_type": error_type,
        "duration_ms": round(duration_ms, 2),
    }
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body),
    )


# ---------------------------------------------------------------------------
# Endpoint: POST /extract-text
# ---------------------------------------------------------------------------


async def handle_extract_text(request: web.Request) -> web.Response:
    """Navigate to URL, extract innerText from selector, persist metadata."""
    start = time.monotonic()

    try:
        body = await request.json()
    except Exception:
        return _error_response("Invalid JSON body", "parse_error", 0, status=400)

    url: str | None = body.get("url")
    selector: str = body.get("selector", "body")

    if not url:
        return _error_response("'url' field is required", "validation_error", 0, status=400)

    request_id = str(uuid.uuid4())
    logger = log.bind(request_id=request_id, url=url, selector=selector, endpoint="extract-text")
    logger.info("extract_text_start")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    text = await page.inner_text(selector, timeout=10_000)
                    page_title = await page.title()
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.error("extract_text_error", error=str(exc), error_type=type(exc).__name__)
        return _error_response(str(exc), type(exc).__name__, duration_ms)

    duration_ms = (time.monotonic() - start) * 1000

    _ensure_dirs()
    metadata = {
        "id": request_id,
        "type": "extract-text",
        "url": url,
        "selector": selector,
        "title": page_title,
        "text": text,
        "timestamp": _now_iso(),
        "duration_ms": round(duration_ms, 2),
    }
    meta_path = SCREENSHOTS_DIR / f"{request_id}.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    logger.info("extract_text_done", duration_ms=round(duration_ms, 2), text_length=len(text))

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(
            {
                "id": request_id,
                "url": url,
                "selector": selector,
                "title": page_title,
                "text": text,
                "duration_ms": round(duration_ms, 2),
            }
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint: POST /screenshot
# ---------------------------------------------------------------------------


async def handle_screenshot(request: web.Request) -> web.Response:
    """Navigate to URL, capture full-page screenshot, persist PNG + metadata."""
    start = time.monotonic()

    try:
        body = await request.json()
    except Exception:
        return _error_response("Invalid JSON body", "parse_error", 0, status=400)

    url: str | None = body.get("url")
    selector: str | None = body.get("selector")

    if not url:
        return _error_response("'url' field is required", "validation_error", 0, status=400)

    request_id = str(uuid.uuid4())
    logger = log.bind(request_id=request_id, url=url, endpoint="screenshot")
    logger.info("screenshot_start")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    page_title = await page.title()

                    _ensure_dirs()
                    png_path = SCREENSHOTS_DIR / f"{request_id}.png"

                    if selector:
                        element = await page.query_selector(selector)
                        if element is None:
                            duration_ms = (time.monotonic() - start) * 1000
                            return _error_response(
                                f"Selector '{selector}' matched no elements",
                                "selector_not_found",
                                duration_ms,
                                status=404,
                            )
                        await element.screenshot(path=str(png_path))
                    else:
                        await page.screenshot(path=str(png_path), full_page=True)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.error("screenshot_error", error=str(exc), error_type=type(exc).__name__)
        return _error_response(str(exc), type(exc).__name__, duration_ms)

    duration_ms = (time.monotonic() - start) * 1000
    screenshot_url = f"/screenshots/{request_id}.png"

    metadata = {
        "id": request_id,
        "type": "screenshot",
        "url": url,
        "selector": selector,
        "title": page_title,
        "screenshot_path": str(png_path),
        "screenshot_url": screenshot_url,
        "timestamp": _now_iso(),
        "duration_ms": round(duration_ms, 2),
    }
    meta_path = SCREENSHOTS_DIR / f"{request_id}.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    logger.info("screenshot_done", duration_ms=round(duration_ms, 2), path=str(png_path))

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(
            {
                "id": request_id,
                "url": url,
                "title": page_title,
                "screenshot_url": screenshot_url,
                "duration_ms": round(duration_ms, 2),
            }
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint: GET /gallery
# ---------------------------------------------------------------------------

GALLERY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Browser Sidecar Gallery</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #e0e0e0; font-family: system-ui, sans-serif; min-height: 100vh; }
  header { padding: 1.25rem 1.5rem; background: #1a1a1a; border-bottom: 1px solid #2a2a2a; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  header h1 { font-size: 1.15rem; font-weight: 600; color: #fff; letter-spacing: 0.02em; flex: 0 0 auto; }
  #filter { flex: 1 1 200px; max-width: 400px; padding: 0.45rem 0.75rem; background: #222; border: 1px solid #333; border-radius: 6px; color: #e0e0e0; font-size: 0.9rem; outline: none; }
  #filter:focus { border-color: #555; }
  #count { font-size: 0.82rem; color: #666; margin-left: auto; flex: 0 0 auto; }
  main { padding: 1.5rem; }
  #grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; overflow: hidden; cursor: pointer; transition: border-color 0.15s, transform 0.1s; }
  .card:hover { border-color: #444; transform: translateY(-2px); }
  .thumb { width: 100%; height: 150px; object-fit: cover; display: block; background: #222; }
  .thumb-placeholder { width: 100%; height: 150px; background: #1e1e1e; display: flex; align-items: center; justify-content: center; color: #444; font-size: 2rem; }
  .card-body { padding: 0.75rem; }
  .card-url { font-size: 0.78rem; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 0.35rem; }
  .card-title { font-size: 0.88rem; color: #ccc; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 0.45rem; }
  .card-meta { display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }
  .badge { font-size: 0.7rem; padding: 0.15rem 0.45rem; border-radius: 4px; font-weight: 500; }
  .badge-text { background: #1a3a2a; color: #4caf82; }
  .badge-screenshot { background: #1a2a3a; color: #5aa0e0; }
  .badge-has-text { background: #2a2a1a; color: #c0a040; }
  .card-ts { font-size: 0.72rem; color: #555; margin-left: auto; }
  .empty { color: #555; text-align: center; padding: 3rem; font-size: 0.95rem; }
  /* Overlay */
  #overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 100; overflow-y: auto; }
  #overlay.open { display: flex; align-items: flex-start; justify-content: center; padding: 2rem 1rem; }
  #detail { background: #1a1a1a; border: 1px solid #333; border-radius: 10px; width: 100%; max-width: 900px; overflow: hidden; position: relative; }
  #detail-close { position: absolute; top: 0.75rem; right: 0.75rem; background: #333; border: none; color: #ccc; width: 2rem; height: 2rem; border-radius: 50%; font-size: 1.1rem; cursor: pointer; display: flex; align-items: center; justify-content: center; line-height: 1; }
  #detail-close:hover { background: #444; color: #fff; }
  #detail-inner { display: flex; flex-direction: column; }
  #detail-img-wrap { background: #111; border-bottom: 1px solid #2a2a2a; text-align: center; padding: 1rem; }
  #detail-img { max-width: 100%; max-height: 480px; object-fit: contain; border-radius: 4px; }
  #detail-info { padding: 1rem 1.25rem; }
  #detail-url { font-size: 0.82rem; color: #888; word-break: break-all; margin-bottom: 0.5rem; }
  #detail-title { font-size: 1rem; font-weight: 600; color: #ddd; margin-bottom: 0.75rem; }
  #detail-text-wrap { margin-top: 0.75rem; }
  #detail-text-label { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.4rem; }
  #detail-text { background: #111; border: 1px solid #2a2a2a; border-radius: 6px; padding: 0.75rem; font-size: 0.83rem; color: #bbb; white-space: pre-wrap; max-height: 300px; overflow-y: auto; line-height: 1.5; }
  #detail-ts { font-size: 0.75rem; color: #555; margin-top: 0.75rem; }
</style>
</head>
<body>
<header>
  <h1>Browser Gallery</h1>
  <input id="filter" type="text" placeholder="Filter by URL..." aria-label="Filter by URL">
  <span id="count"></span>
</header>
<main>
  <div id="grid"></div>
</main>
<div id="overlay" role="dialog" aria-modal="true" aria-label="Detail view">
  <div id="detail">
    <button id="detail-close" aria-label="Close detail">×</button>
    <div id="detail-inner">
      <div id="detail-img-wrap" style="display:none">
        <img id="detail-img" alt="Screenshot">
      </div>
      <div id="detail-info">
        <div id="detail-url"></div>
        <div id="detail-title"></div>
        <div id="detail-text-wrap" style="display:none">
          <div id="detail-text-label">Extracted Text</div>
          <div id="detail-text"></div>
        </div>
        <div id="detail-ts"></div>
      </div>
    </div>
  </div>
</div>
<script>
(function () {
  'use strict';

  var items = [];
  var filtered = [];

  function fetchItems() {
    fetch('/gallery/data')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        items = data;
        applyFilter();
      })
      .catch(function (err) {
        var grid = document.getElementById('grid');
        var msg = document.createElement('p');
        msg.className = 'empty';
        msg.textContent = 'Failed to load gallery data.';
        grid.appendChild(msg);
      });
  }

  function applyFilter() {
    var q = document.getElementById('filter').value.toLowerCase();
    filtered = q
      ? items.filter(function (it) { return it.url.toLowerCase().indexOf(q) !== -1; })
      : items.slice();
    renderGrid();
  }

  function renderGrid() {
    var grid = document.getElementById('grid');
    while (grid.firstChild) { grid.removeChild(grid.firstChild); }

    var count = document.getElementById('count');
    count.textContent = filtered.length + ' item' + (filtered.length !== 1 ? 's' : '');

    if (filtered.length === 0) {
      var empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = items.length === 0 ? 'No screenshots or extractions yet.' : 'No results match your filter.';
      grid.appendChild(empty);
      return;
    }

    filtered.forEach(function (item) {
      var card = document.createElement('div');
      card.className = 'card';
      card.setAttribute('tabindex', '0');

      if (item.screenshot_url) {
        var img = document.createElement('img');
        img.className = 'thumb';
        img.src = item.screenshot_url;
        img.alt = 'Screenshot of ' + item.url;
        img.loading = 'lazy';
        card.appendChild(img);
      } else {
        var ph = document.createElement('div');
        ph.className = 'thumb-placeholder';
        ph.textContent = '📄';
        card.appendChild(ph);
      }

      var body = document.createElement('div');
      body.className = 'card-body';

      var urlEl = document.createElement('div');
      urlEl.className = 'card-url';
      urlEl.textContent = item.url;
      body.appendChild(urlEl);

      var titleEl = document.createElement('div');
      titleEl.className = 'card-title';
      titleEl.textContent = item.title || '(no title)';
      body.appendChild(titleEl);

      var metaEl = document.createElement('div');
      metaEl.className = 'card-meta';

      var typeBadge = document.createElement('span');
      typeBadge.className = 'badge ' + (item.type === 'screenshot' ? 'badge-screenshot' : 'badge-text');
      typeBadge.textContent = item.type === 'screenshot' ? 'screenshot' : 'extract';
      metaEl.appendChild(typeBadge);

      if (item.has_text) {
        var textBadge = document.createElement('span');
        textBadge.className = 'badge badge-has-text';
        textBadge.textContent = 'has text';
        metaEl.appendChild(textBadge);
      }

      var ts = document.createElement('span');
      ts.className = 'card-ts';
      ts.textContent = formatTs(item.timestamp);
      metaEl.appendChild(ts);

      body.appendChild(metaEl);
      card.appendChild(body);

      card.addEventListener('click', function () { openDetail(item); });
      card.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(item); }
      });

      grid.appendChild(card);
    });
  }

  function formatTs(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString();
    } catch (e) { return iso; }
  }

  function openDetail(item) {
    var overlay = document.getElementById('overlay');
    var imgWrap = document.getElementById('detail-img-wrap');
    var img = document.getElementById('detail-img');
    var urlEl = document.getElementById('detail-url');
    var titleEl = document.getElementById('detail-title');
    var textWrap = document.getElementById('detail-text-wrap');
    var textEl = document.getElementById('detail-text');
    var tsEl = document.getElementById('detail-ts');

    urlEl.textContent = item.url;
    titleEl.textContent = item.title || '(no title)';
    tsEl.textContent = item.timestamp ? 'Captured: ' + formatTs(item.timestamp) : '';

    if (item.screenshot_url) {
      img.src = item.screenshot_url;
      img.alt = 'Screenshot of ' + item.url;
      imgWrap.style.display = '';
    } else {
      imgWrap.style.display = 'none';
      img.src = '';
    }

    if (item.text) {
      textEl.textContent = item.text;
      textWrap.style.display = '';
    } else {
      textWrap.style.display = 'none';
      textEl.textContent = '';
    }

    overlay.classList.add('open');
    document.getElementById('detail-close').focus();
  }

  function closeDetail() {
    var overlay = document.getElementById('overlay');
    overlay.classList.remove('open');
  }

  document.getElementById('detail-close').addEventListener('click', closeDetail);
  document.getElementById('overlay').addEventListener('click', function (e) {
    if (e.target === this) { closeDetail(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeDetail(); }
  });
  document.getElementById('filter').addEventListener('input', applyFilter);

  fetchItems();
})();
</script>
</body>
</html>
"""


async def handle_gallery(request: web.Request) -> web.Response:
    """Serve the gallery HTML page."""
    return web.Response(status=200, content_type="text/html", text=GALLERY_HTML)


async def handle_gallery_data(request: web.Request) -> web.Response:
    """Return JSON index of all saved extractions and screenshots."""
    _ensure_dirs()
    items: list[dict] = []
    for meta_file in sorted(SCREENSHOTS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(meta_file.read_text())
            item = {
                "id": data.get("id", meta_file.stem),
                "type": data.get("type", "unknown"),
                "url": data.get("url", ""),
                "title": data.get("title", ""),
                "timestamp": data.get("timestamp", ""),
                "screenshot_url": data.get("screenshot_url"),
                "has_text": bool(data.get("text")),
                "text": data.get("text"),
            }
            items.append(item)
        except Exception as exc:
            log.warning("gallery_skip_bad_json", file=str(meta_file), error=str(exc))

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(items),
    )


# ---------------------------------------------------------------------------
# Static file handler for /screenshots/
# ---------------------------------------------------------------------------


async def handle_static_screenshot(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    # Sanitize: allow only alphanumeric, dash, underscore, dot
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if not all(c in safe_chars for c in filename) or ".." in filename:
        return web.Response(status=400, text="Invalid filename")
    file_path = SCREENSHOTS_DIR / filename
    if not file_path.exists():
        return web.Response(status=404, text="Not found")
    content_type = "image/png" if filename.endswith(".png") else "application/octet-stream"
    return web.Response(
        status=200,
        content_type=content_type,
        body=file_path.read_bytes(),
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/extract-text", handle_extract_text)
    app.router.add_post("/screenshot", handle_screenshot)
    app.router.add_get("/gallery", handle_gallery)
    app.router.add_get("/gallery/data", handle_gallery_data)
    app.router.add_get("/screenshots/{filename}", handle_static_screenshot)
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _ensure_dirs()
    log.info("browser_sidecar_starting", port=PORT, data_dir=str(DATA_DIR))
    app = build_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
