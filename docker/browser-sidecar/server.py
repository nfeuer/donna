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

# Limit concurrent Chromium instances to avoid resource exhaustion.
_browser_semaphore = asyncio.Semaphore(3)

# ---------------------------------------------------------------------------
# Stealth / anti-detection configuration
# ---------------------------------------------------------------------------

CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--lang=en-US",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

STEALTH_JS = """\
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

window.chrome = {
    runtime: {},
    loadTimes: function() { return {}; },
    csi: function() { return {}; },
    app: {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    },
};

const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) => (
    params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params)
);

Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        p.length = 3;
        return p;
    },
});

Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Google Inc. (NVIDIA)';
    if (p === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return getParam.call(this, p);
};

try {
    const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    Object.defineProperty(HTMLDivElement.prototype, 'offsetHeight', {
        ...desc,
        get: function() { if (this.id === 'modernizr') return 1; return desc.get.apply(this); },
    });
} catch (e) {}
"""

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


def _is_tls_error(exc: Exception) -> bool:
    msg = str(exc)
    return "SSL" in msg or "CIPHER" in msg or "ERR_SSL" in msg


async def _new_stealth_context(browser, viewport=None):
    ctx = await browser.new_context(
        user_agent=DEFAULT_USER_AGENT,
        viewport=viewport or {"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    return ctx


async def _new_stealth_page(context):
    page = await context.new_page()
    await page.add_init_script(STEALTH_JS)
    return page


COOKIE_ACCEPT_SELECTORS = [
    "button[id*='accept' i]",
    "button[class*='accept' i]",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('ACCEPT ALL')",
    "button:has-text('Accept Cookies')",
    "button:has-text('Allow All')",
    "button:has-text('Allow all')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "[data-testid*='accept' i]",
    "#onetrust-accept-btn-handler",
    ".cookie-accept",
]


async def _dismiss_cookie_banner(page):
    for sel in COOKIE_ACCEPT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=1000)
                await page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    return False


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
    timeout_ms: int = min(int(body.get("timeout_ms", 15_000)), 60_000)
    automation_id: str | None = body.get("automation_id", None)

    if not url:
        return _error_response("'url' field is required", "validation_error", 0, status=400)

    request_id = str(uuid.uuid4())
    timestamp = _now_iso()
    logger = log.bind(request_id=request_id, url=url, selector=selector, action="extract-text")
    logger.info("extract_text_start")

    status_str = "success"
    error_str: str | None = None
    text = ""
    page_title = ""

    try:
        async with _browser_semaphore:
            async with async_playwright() as pw:
                for channel in ["chrome", None]:
                    launch_kw = {"headless": True, "args": CHROMIUM_ARGS}
                    if channel:
                        launch_kw["channel"] = channel
                    browser = await pw.chromium.launch(**launch_kw)
                    try:
                        context = await _new_stealth_context(browser)
                        try:
                            page = await _new_stealth_page(context)
                            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            try:
                                await page.wait_for_load_state("load", timeout=10_000)
                            except Exception:
                                pass
                            try:
                                await page.wait_for_function(
                                    "document.body && document.body.innerText.trim().length > 100",
                                    timeout=8_000,
                                )
                            except Exception:
                                pass
                            await _dismiss_cookie_banner(page)
                            text = await page.inner_text(selector, timeout=timeout_ms)
                            page_title = await page.title()
                            break
                        finally:
                            await context.close()
                    except Exception as exc:
                        if channel and _is_tls_error(exc):
                            logger.info("chrome_tls_fallback", url=url)
                            continue
                        raise
                    finally:
                        await browser.close()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        exc_name = type(exc).__name__
        status_str = "timeout" if "timeout" in exc_name.lower() or "timeout" in str(exc).lower() else "error"
        error_str = str(exc)
        logger.error(
            "extract_text_error",
            url=url,
            action="extract-text",
            duration_ms=round(duration_ms, 2),
            response_bytes=0,
            status=status_str,
            error=error_str,
        )
        return _error_response(str(exc), exc_name, duration_ms)

    duration_ms = (time.monotonic() - start) * 1000
    response_bytes = len(text.encode("utf-8"))

    _ensure_dirs()
    metadata = {
        "url": url,
        "timestamp": timestamp,
        "text": text,
        "selector": selector,
        "duration_ms": round(duration_ms, 2),
        "automation_id": automation_id,
        "screenshot_path": None,
        # internal bookkeeping fields (not part of spec storage format but useful)
        "id": request_id,
        "type": "extract-text",
        "title": page_title,
    }
    meta_path = SCREENSHOTS_DIR / f"{request_id}.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    logger.info(
        "extract_text_done",
        url=url,
        action="extract-text",
        duration_ms=round(duration_ms, 2),
        response_bytes=response_bytes,
        status=status_str,
        error=None,
    )

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(
            {
                "text": text,
                "url": url,
                "selector_used": selector,
                "timestamp": timestamp,
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
    timeout_ms: int = min(int(body.get("timeout_ms", 15_000)), 60_000)
    automation_id: str | None = body.get("automation_id", None)

    if not url:
        return _error_response("'url' field is required", "validation_error", 0, status=400)

    request_id = str(uuid.uuid4())
    timestamp = _now_iso()
    logger = log.bind(request_id=request_id, url=url, action="screenshot")
    logger.info("screenshot_start")

    status_str = "success"
    error_str: str | None = None
    page_title = ""
    png_path: Path | None = None

    try:
        async with _browser_semaphore:
            async with async_playwright() as pw:
                for channel in ["chrome", None]:
                    launch_kw = {"headless": True, "args": CHROMIUM_ARGS}
                    if channel:
                        launch_kw["channel"] = channel
                    browser = await pw.chromium.launch(**launch_kw)
                    try:
                        context = await _new_stealth_context(browser, viewport={"width": 1280, "height": 800})
                        try:
                            page = await _new_stealth_page(context)
                            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            try:
                                await page.wait_for_load_state("load", timeout=10_000)
                            except Exception:
                                pass
                            try:
                                await page.wait_for_function(
                                    "document.body && document.body.innerText.trim().length > 100",
                                    timeout=8_000,
                                )
                            except Exception:
                                pass
                            await _dismiss_cookie_banner(page)
                            page_title = await page.title()

                            _ensure_dirs()
                            png_path = SCREENSHOTS_DIR / f"{request_id}.png"

                            if selector:
                                element = await page.query_selector(selector)
                                if element is None:
                                    duration_ms = (time.monotonic() - start) * 1000
                                    logger.error(
                                        "screenshot_error",
                                        url=url,
                                        action="screenshot",
                                        duration_ms=round(duration_ms, 2),
                                        response_bytes=0,
                                        status="error",
                                        error=f"Selector '{selector}' matched no elements",
                                    )
                                    return _error_response(
                                        f"Selector '{selector}' matched no elements",
                                        "selector_not_found",
                                        duration_ms,
                                        status=404,
                                    )
                                await element.screenshot(path=str(png_path))
                            else:
                                await page.screenshot(path=str(png_path), full_page=True)
                            break
                        finally:
                            await context.close()
                    except Exception as exc:
                        if channel and _is_tls_error(exc):
                            logger.info("chrome_tls_fallback", url=url)
                            continue
                        raise
                    finally:
                        await browser.close()
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        exc_name = type(exc).__name__
        status_str = "timeout" if "timeout" in exc_name.lower() or "timeout" in str(exc).lower() else "error"
        error_str = str(exc)
        logger.error(
            "screenshot_error",
            url=url,
            action="screenshot",
            duration_ms=round(duration_ms, 2),
            response_bytes=0,
            status=status_str,
            error=error_str,
        )
        return _error_response(str(exc), exc_name, duration_ms)

    duration_ms = (time.monotonic() - start) * 1000
    file_path_str = str(png_path)
    response_bytes = png_path.stat().st_size if png_path and png_path.exists() else 0

    metadata = {
        "url": url,
        "timestamp": timestamp,
        "text": None,
        "selector": selector,
        "duration_ms": round(duration_ms, 2),
        "automation_id": automation_id,
        "screenshot_path": file_path_str,
        # internal bookkeeping fields
        "id": request_id,
        "type": "screenshot",
        "title": page_title,
        # keep screenshot_url for gallery backward compat
        "screenshot_url": f"/screenshots/{request_id}.png",
    }
    meta_path = SCREENSHOTS_DIR / f"{request_id}.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    logger.info(
        "screenshot_done",
        url=url,
        action="screenshot",
        duration_ms=round(duration_ms, 2),
        response_bytes=response_bytes,
        status=status_str,
        error=None,
    )

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(
            {
                "file_path": file_path_str,
                "page_title": page_title,
                "url": url,
                "timestamp": timestamp,
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
  header { padding: 1.25rem 1.5rem; background: #1a1a1a; border-bottom: 1px solid #2a2a2a; display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  header h1 { font-size: 1.15rem; font-weight: 600; color: #fff; letter-spacing: 0.02em; flex: 0 0 auto; }
  .filter-input { flex: 1 1 180px; max-width: 340px; padding: 0.45rem 0.75rem; background: #222; border: 1px solid #333; border-radius: 6px; color: #e0e0e0; font-size: 0.9rem; outline: none; }
  .filter-input:focus { border-color: #555; }
  .date-label { font-size: 0.8rem; color: #888; flex: 0 0 auto; }
  .date-input { padding: 0.42rem 0.6rem; background: #222; border: 1px solid #333; border-radius: 6px; color: #e0e0e0; font-size: 0.85rem; outline: none; color-scheme: dark; }
  .date-input:focus { border-color: #555; }
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
  #detail { background: #1a1a1a; border: 1px solid #333; border-radius: 10px; width: 100%; max-width: 1100px; overflow: hidden; position: relative; }
  #detail-close { position: absolute; top: 0.75rem; right: 0.75rem; background: #333; border: none; color: #ccc; width: 2rem; height: 2rem; border-radius: 50%; font-size: 1.1rem; cursor: pointer; display: flex; align-items: center; justify-content: center; line-height: 1; z-index: 1; }
  #detail-close:hover { background: #444; color: #fff; }
  #detail-inner { display: flex; flex-direction: row; align-items: flex-start; min-height: 300px; }
  #detail-img-wrap { flex: 0 0 55%; background: #111; border-right: 1px solid #2a2a2a; text-align: center; padding: 1rem; align-self: stretch; display: flex; align-items: center; justify-content: center; }
  #detail-img { max-width: 100%; max-height: 520px; object-fit: contain; border-radius: 4px; }
  #detail-info { flex: 1 1 0; padding: 1rem 1.25rem; overflow-y: auto; max-height: 600px; }
  #detail-url { font-size: 0.82rem; color: #888; word-break: break-all; margin-bottom: 0.5rem; }
  #detail-title { font-size: 1rem; font-weight: 600; color: #ddd; margin-bottom: 0.75rem; }
  #detail-text-wrap { margin-top: 0.75rem; }
  #detail-text-label { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.4rem; }
  #detail-text { background: #111; border: 1px solid #2a2a2a; border-radius: 6px; padding: 0.75rem; font-size: 0.83rem; color: #bbb; white-space: pre-wrap; max-height: 400px; overflow-y: auto; line-height: 1.5; }
  #detail-ts { font-size: 0.75rem; color: #555; margin-top: 0.75rem; }
</style>
</head>
<body>
<header>
  <h1>Browser Gallery</h1>
  <input id="filter" class="filter-input" type="text" placeholder="Filter by URL..." aria-label="Filter by URL">
  <span class="date-label">From</span>
  <input id="date-start" class="date-input" type="date" aria-label="Start date">
  <span class="date-label">To</span>
  <input id="date-end" class="date-input" type="date" aria-label="End date">
  <span id="count"></span>
</header>
<main>
  <div id="grid"></div>
</main>
<div id="overlay" role="dialog" aria-modal="true" aria-label="Detail view">
  <div id="detail">
    <button id="detail-close" aria-label="Close detail">&times;</button>
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
    var startVal = document.getElementById('date-start').value;
    var endVal = document.getElementById('date-end').value;
    var startMs = startVal ? new Date(startVal).getTime() : null;
    // end date: include the full end day
    var endMs = endVal ? new Date(endVal).getTime() + 86400000 - 1 : null;

    filtered = items.filter(function (it) {
      if (q && it.url.toLowerCase().indexOf(q) === -1) { return false; }
      if (it.timestamp) {
        var ts = new Date(it.timestamp).getTime();
        if (startMs !== null && ts < startMs) { return false; }
        if (endMs !== null && ts > endMs) { return false; }
      } else {
        // item has no timestamp — exclude if a date range is set
        if (startMs !== null || endMs !== null) { return false; }
      }
      return true;
    });
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
  document.getElementById('date-start').addEventListener('change', applyFilter);
  document.getElementById('date-end').addEventListener('change', applyFilter);

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
