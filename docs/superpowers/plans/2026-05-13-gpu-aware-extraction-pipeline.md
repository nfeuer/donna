# GPU-Aware Multi-Tier Extraction Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the product_watch pipeline with a multi-tier extraction system backed by a Playwright browser sidecar, GPU-aware queue scheduling, and cascading local→cloud tiers.

**Architecture:** Playwright sidecar (Docker) handles rendering/extraction. LLM Gateway Queue gains GPU model tracking, model-affinity sorting, and home-model restore. Automation scheduler groups same-model work. Three tiers cascade: text→vision→Claude tool_use.

**Tech Stack:** Python 3.12, Playwright (Chromium headless), Ollama, Claude API tool_use, Docker Compose, FastAPI, aiosqlite, Alembic, React + CSS Modules.

**Spec:** `docs/superpowers/specs/2026-05-13-gpu-aware-extraction-pipeline-design.md`

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `docker/browser-sidecar/server.py` | Create | Playwright HTTP service (extract-text, screenshot, gallery) |
| `docker/browser-sidecar/Dockerfile` | Create | Container image for sidecar |
| `docker/browser-sidecar/requirements.txt` | Create | Python deps for sidecar |
| `docker/donna-app.yml` | Modify | Add donna-browser service + browser-data volume |
| `docker/donna-core.yml` | Modify | Mount browser-data volume on orchestrator |
| `src/donna/skills/tools/browser_extract_text.py` | Create | Tool: calls sidecar /extract-text |
| `src/donna/skills/tools/browser_screenshot.py` | Create | Tool: calls sidecar /screenshot |
| `src/donna/skills/tools/__init__.py` | Modify | Register browser tools |
| `src/donna/llm/types.py` | Modify | Add required_model to QueueItem, GpuConfig dataclass |
| `config/llm_gateway.yaml` | Modify | Add gpu: section |
| `src/donna/models/providers/ollama.py` | Modify | Add list_running() method |
| `src/donna/llm/gpu_tracker.py` | Create | GpuTracker: model state, swap metrics, alerts |
| `src/donna/llm/queue.py` | Modify | GPU-aware _pop_next, swap coordination, home restore |
| `alembic/versions/xxxx_add_gpu_model_fields.py` | Create | Migration: gpu_model, preferred_window columns |
| `src/donna/automations/models.py` | Modify | Add gpu_model, preferred_window to AutomationRow |
| `src/donna/automations/scheduler.py` | Modify | Model-affinity grouping for due automations |
| `src/donna/skills/executor.py` | Modify | Conditional step support (condition field) |
| `skills/product_watch/skill.yaml` | Modify | Multi-tier pipeline with conditions |
| `skills/product_watch/steps/extract_product_info.md` | Modify | Reference Playwright text instead of HTML |
| `skills/product_watch/steps/extract_from_screenshot.md` | Create | Vision extraction prompt |
| `skills/product_watch/steps/extract_via_claude.md` | Create | Claude tool_use extraction prompt |
| `skills/product_watch/steps/format_output.md` | Modify | Multi-tier aware output |
| `src/donna/api/routes/automations.py` | Modify | Tier stats endpoint, gpu_model fields |
| `donna-ui/src/pages/SkillSystem/` | Modify | Tier pills, GPU card, gallery link |
| `donna-ui/src/api/skillSystem.ts` | Modify | Tier stats API call |
| `tests/llm/test_gpu_tracker.py` | Create | GpuTracker unit tests |
| `tests/llm/test_queue_gpu.py` | Create | GPU-aware queue tests |
| `tests/skills/test_executor_conditions.py` | Create | Conditional step tests |
| `tests/automations/test_scheduler_affinity.py` | Create | Scheduler grouping tests |
| `tests/tools/test_browser_tools.py` | Create | Browser tool tests |

---

### Task 1: Playwright Browser Sidecar

**Files:**
- Create: `docker/browser-sidecar/server.py`
- Create: `docker/browser-sidecar/Dockerfile`
- Create: `docker/browser-sidecar/requirements.txt`
- Test: Manual — `docker build` + `curl` tests

- [ ] **Step 1: Create requirements.txt**

```
playwright==1.52.0
aiohttp==3.11.0
structlog==24.4.0
```

- [ ] **Step 2: Create server.py**

```python
"""Playwright browser sidecar — text extraction, screenshots, and gallery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from aiohttp import web
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logger = structlog.get_logger()

DATA_DIR = Path(os.environ.get("BROWSER_DATA_DIR", "/data/browser"))
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

_playwright = None
_browser = None


async def _get_browser():
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
    return _browser


def _make_id() -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S")
    h = hashlib.md5(f"{ts}{time.monotonic_ns()}".encode()).hexdigest()[:8]
    return f"{ts}_{h}"


async def handle_extract_text(request: web.Request) -> web.Response:
    body = await request.json()
    url = body.get("url")
    if not url:
        return web.json_response({"error": "url is required"}, status=400)

    selector = body.get("selector", "body")
    timeout_ms = body.get("timeout_ms", 15000)
    start = time.monotonic()

    browser = await _get_browser()
    context = await browser.new_context()
    page = await context.new_page()

    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        text = await page.inner_text(selector, timeout=timeout_ms)
        duration_ms = int((time.monotonic() - start) * 1000)

        file_id = _make_id()
        meta = {
            "url": url,
            "timestamp": datetime.now(UTC).isoformat(),
            "text": text,
            "selector": selector,
            "duration_ms": duration_ms,
            "screenshot_path": None,
        }
        meta_path = SCREENSHOTS_DIR / f"{file_id}.json"
        meta_path.write_text(json.dumps(meta, indent=2))

        logger.info("extract_text", url=url, duration_ms=duration_ms,
                     response_bytes=len(text), status="success")

        return web.json_response({
            "text": text, "url": url, "selector_used": selector,
            "timestamp": meta["timestamp"], "duration_ms": duration_ms,
        })
    except PlaywrightTimeout:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("extract_text_timeout", url=url, duration_ms=duration_ms)
        return web.json_response({
            "error": "timeout", "error_type": "timeout",
            "url": url, "duration_ms": duration_ms,
        }, status=504)
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("extract_text_error", url=url, error=str(exc),
                      duration_ms=duration_ms)
        return web.json_response({
            "error": str(exc), "error_type": "navigation_error",
            "url": url, "duration_ms": duration_ms,
        }, status=502)
    finally:
        await context.close()


async def handle_screenshot(request: web.Request) -> web.Response:
    body = await request.json()
    url = body.get("url")
    if not url:
        return web.json_response({"error": "url is required"}, status=400)

    timeout_ms = body.get("timeout_ms", 15000)
    start = time.monotonic()

    browser = await _get_browser()
    context = await browser.new_context()
    page = await context.new_page()

    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        title = await page.title()

        file_id = _make_id()
        png_path = SCREENSHOTS_DIR / f"{file_id}.png"
        await page.screenshot(path=str(png_path), full_page=True)
        duration_ms = int((time.monotonic() - start) * 1000)

        meta = {
            "url": url,
            "timestamp": datetime.now(UTC).isoformat(),
            "text": None,
            "selector": None,
            "duration_ms": duration_ms,
            "screenshot_path": f"{file_id}.png",
        }
        meta_path = SCREENSHOTS_DIR / f"{file_id}.json"
        meta_path.write_text(json.dumps(meta, indent=2))

        logger.info("screenshot", url=url, duration_ms=duration_ms,
                     response_bytes=png_path.stat().st_size, status="success")

        return web.json_response({
            "file_path": str(png_path), "page_title": title,
            "url": url, "timestamp": meta["timestamp"],
            "duration_ms": duration_ms,
        })
    except PlaywrightTimeout:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("screenshot_timeout", url=url, duration_ms=duration_ms)
        return web.json_response({
            "error": "timeout", "error_type": "timeout",
            "url": url, "duration_ms": duration_ms,
        }, status=504)
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("screenshot_error", url=url, error=str(exc),
                      duration_ms=duration_ms)
        return web.json_response({
            "error": str(exc), "error_type": "navigation_error",
            "url": url, "duration_ms": duration_ms,
        }, status=502)
    finally:
        await context.close()


async def handle_gallery(request: web.Request) -> web.Response:
    url_filter = request.query.get("url", "")
    items = []
    for meta_file in sorted(SCREENSHOTS_DIR.glob("*.json"), reverse=True):
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if url_filter and url_filter not in meta.get("url", ""):
            continue
        items.append(meta)

    html = _build_gallery_html(items, url_filter)
    return web.Response(text=html, content_type="text/html")


def _build_gallery_html(items: list[dict], url_filter: str) -> str:
    items_json = json.dumps(items)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Browser Extraction Gallery</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; padding: 1rem; }}
  .filter {{ margin-bottom: 1rem; display: flex; gap: 0.5rem; }}
  .filter input {{ flex: 1; padding: 0.5rem; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }}
  .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 6px; overflow: hidden; cursor: pointer; }}
  .card img {{ width: 100%; height: 180px; object-fit: cover; }}
  .card .info {{ padding: 0.75rem; }}
  .card .url {{ font-size: 0.8rem; color: #999; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .card .ts {{ font-size: 0.75rem; color: #666; margin-top: 0.25rem; }}
  .card .badge {{ display: inline-block; font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; background: #2a5; color: #fff; margin-top: 0.25rem; }}
  .no-img {{ height: 180px; display: flex; align-items: center; justify-content: center; background: #222; color: #666; }}
  .detail {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 100; overflow: auto; padding: 2rem; }}
  .detail.open {{ display: flex; gap: 2rem; }}
  .detail img {{ max-width: 50%; object-fit: contain; }}
  .detail .text {{ flex: 1; white-space: pre-wrap; font-size: 0.9rem; color: #ccc; overflow: auto; }}
  .detail .close {{ position: fixed; top: 1rem; right: 1rem; font-size: 2rem; cursor: pointer; color: #fff; }}
</style>
</head><body>
<h1>Extraction Gallery</h1>
<div class="filter">
  <input id="url-filter" type="text" placeholder="Filter by URL..." value="{url_filter}">
</div>
<div class="grid" id="grid"></div>
<div class="detail" id="detail">
  <span class="close" id="close-btn">&times;</span>
  <img id="detail-img" src="" alt="">
  <div class="text" id="detail-text"></div>
</div>
<script>
const items = {items_json};
const grid = document.getElementById('grid');
const detail = document.getElementById('detail');
const detailImg = document.getElementById('detail-img');
const detailText = document.getElementById('detail-text');
const closeBtn = document.getElementById('close-btn');
const urlFilter = document.getElementById('url-filter');

function renderGrid(data) {{
  while (grid.firstChild) grid.removeChild(grid.firstChild);
  data.forEach(function(item) {{
    const card = document.createElement('div');
    card.className = 'card';

    if (item.screenshot_path) {{
      const img = document.createElement('img');
      img.src = '/screenshots/' + item.screenshot_path;
      img.alt = item.url || '';
      card.appendChild(img);
    }} else {{
      const noImg = document.createElement('div');
      noImg.className = 'no-img';
      noImg.textContent = 'Text only';
      card.appendChild(noImg);
    }}

    const info = document.createElement('div');
    info.className = 'info';
    const urlDiv = document.createElement('div');
    urlDiv.className = 'url';
    urlDiv.textContent = item.url || '';
    info.appendChild(urlDiv);

    const tsDiv = document.createElement('div');
    tsDiv.className = 'ts';
    tsDiv.textContent = item.timestamp || '';
    info.appendChild(tsDiv);

    if (item.text) {{
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = 'Has text';
      info.appendChild(badge);
    }}

    card.appendChild(info);
    card.addEventListener('click', function() {{ showDetail(item); }});
    grid.appendChild(card);
  }});
}}

function showDetail(item) {{
  if (item.screenshot_path) {{
    detailImg.src = '/screenshots/' + item.screenshot_path;
    detailImg.style.display = 'block';
  }} else {{
    detailImg.style.display = 'none';
  }}
  detailText.textContent = item.text || '(no text extracted)';
  detail.classList.add('open');
}}

closeBtn.addEventListener('click', function() {{ detail.classList.remove('open'); }});
detail.addEventListener('click', function(e) {{ if (e.target === detail) detail.classList.remove('open'); }});
urlFilter.addEventListener('input', function() {{
  const q = urlFilter.value.toLowerCase();
  renderGrid(items.filter(function(i) {{ return !q || (i.url && i.url.toLowerCase().indexOf(q) !== -1); }}));
}});

renderGrid(items);
</script>
</body></html>"""


async def on_startup(app: web.Application) -> None:
    await _get_browser()
    logger.info("browser_sidecar_started", data_dir=str(DATA_DIR))


async def on_shutdown(app: web.Application) -> None:
    global _browser, _playwright
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


app = web.Application()
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)
app.router.add_post("/extract-text", handle_extract_text)
app.router.add_post("/screenshot", handle_screenshot)
app.router.add_get("/gallery", handle_gallery)
app.router.add_static("/screenshots", str(SCREENSHOTS_DIR))

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=3100)
```

- [ ] **Step 3: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium

COPY server.py .

EXPOSE 3100
ENV BROWSER_DATA_DIR=/data/browser

CMD ["python", "server.py"]
```

- [ ] **Step 4: Commit**

```bash
git add docker/browser-sidecar/
git commit -m "feat(browser): add Playwright browser sidecar with extract-text, screenshot, and gallery"
```

---

### Task 2: Docker Compose Integration

**Files:**
- Modify: `docker/donna-app.yml`
- Modify: `docker/donna-core.yml`

- [ ] **Step 1: Add donna-browser service to donna-app.yml**

Add after the `donna-api` service, before the `networks:` section:

```yaml
  donna-browser:
    build:
      context: ..
      dockerfile: docker/browser-sidecar/Dockerfile
    container_name: donna-browser
    restart: unless-stopped
    environment:
      - BROWSER_DATA_DIR=/data/browser
    volumes:
      - browser-data:/data/browser
    ports:
      - "3100:3100"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3100/gallery"]
      interval: 30s
      timeout: 10s
      retries: 3
    networks:
      - homelab
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Add volumes section at top level (after networks):

```yaml
volumes:
  browser-data:
```

- [ ] **Step 2: Mount browser-data volume on orchestrator in donna-core.yml**

Add to `donna-orchestrator.volumes`:

```yaml
      - browser-data:/data/browser
```

Add to top-level volumes:

```yaml
volumes:
  browser-data:
    external: true
```

- [ ] **Step 3: Commit**

```bash
git add docker/donna-app.yml docker/donna-core.yml
git commit -m "feat(docker): integrate browser sidecar with shared volume"
```

---

### Task 3: Browser Tools for Orchestrator

**Files:**
- Create: `src/donna/skills/tools/browser_extract_text.py`
- Create: `src/donna/skills/tools/browser_screenshot.py`
- Modify: `src/donna/skills/tools/__init__.py`
- Test: `tests/tools/test_browser_tools.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for browser_extract_text and browser_screenshot tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from donna.skills.tools.browser_extract_text import browser_extract_text
from donna.skills.tools.browser_screenshot import browser_screenshot


@pytest.mark.asyncio
async def test_browser_extract_text_success():
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "text": "Nike Air Max 90\n$129.99",
        "url": "https://example.com/product",
        "selector_used": "main",
        "timestamp": "2026-05-13T03:00:12Z",
        "duration_ms": 2340,
    })

    mock_session = AsyncMock()
    mock_session.post = AsyncMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_resp),
        __aexit__=AsyncMock(return_value=False),
    ))

    with patch("donna.skills.tools.browser_extract_text.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=AsyncMock(
            status_code=200,
            json=lambda: {
                "text": "Nike Air Max 90\n$129.99",
                "url": "https://example.com/product",
                "selector_used": "main",
                "timestamp": "2026-05-13T03:00:12Z",
                "duration_ms": 2340,
            },
            raise_for_status=lambda: None,
        ))
        mock_cls.return_value = mock_client

        result = await browser_extract_text(url="https://example.com/product", selector="main")
        assert result["text"] == "Nike Air Max 90\n$129.99"
        assert result["url"] == "https://example.com/product"


@pytest.mark.asyncio
async def test_browser_screenshot_success():
    with patch("donna.skills.tools.browser_screenshot.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=AsyncMock(
            status_code=200,
            json=lambda: {
                "file_path": "/data/browser/screenshots/test.png",
                "page_title": "Test Product",
                "url": "https://example.com/product",
                "timestamp": "2026-05-13T03:00:12Z",
                "duration_ms": 3100,
            },
            raise_for_status=lambda: None,
        ))
        mock_cls.return_value = mock_client

        result = await browser_screenshot(url="https://example.com/product")
        assert result["file_path"] == "/data/browser/screenshots/test.png"
        assert result["page_title"] == "Test Product"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_browser_tools.py -v`
Expected: FAIL (modules not found)

- [ ] **Step 3: Create browser_extract_text.py**

```python
"""browser_extract_text — calls the Playwright sidecar /extract-text endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

BROWSER_SIDECAR_URL = os.environ.get("BROWSER_SIDECAR_URL", "http://donna-browser:3100")


class BrowserExtractError(Exception):
    """Raised when text extraction via the browser sidecar fails."""


async def browser_extract_text(
    url: str,
    selector: str = "body",
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Extract text from a URL via the Playwright sidecar.

    Args:
        url: The page URL to extract text from.
        selector: CSS selector to extract innerText from.
        timeout_ms: Navigation timeout in milliseconds.

    Returns:
        Dict with text, url, selector_used, timestamp, duration_ms.
    """
    payload = {"url": url, "selector": selector, "timeout_ms": timeout_ms}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BROWSER_SIDECAR_URL}/extract-text", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("browser_extract_text_failed", url=url, error=str(exc))
        raise BrowserExtractError(str(exc)) from exc

    logger.info(
        "browser_extract_text",
        url=url,
        duration_ms=data.get("duration_ms"),
        text_length=len(data.get("text", "")),
    )
    return data
```

- [ ] **Step 4: Create browser_screenshot.py**

```python
"""browser_screenshot — calls the Playwright sidecar /screenshot endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

BROWSER_SIDECAR_URL = os.environ.get("BROWSER_SIDECAR_URL", "http://donna-browser:3100")


class BrowserScreenshotError(Exception):
    """Raised when screenshot capture via the browser sidecar fails."""


async def browser_screenshot(
    url: str,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Capture a full-page screenshot via the Playwright sidecar.

    Args:
        url: The page URL to screenshot.
        timeout_ms: Navigation timeout in milliseconds.

    Returns:
        Dict with file_path, page_title, url, timestamp, duration_ms.
    """
    payload = {"url": url, "timeout_ms": timeout_ms}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BROWSER_SIDECAR_URL}/screenshot", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("browser_screenshot_failed", url=url, error=str(exc))
        raise BrowserScreenshotError(str(exc)) from exc

    logger.info(
        "browser_screenshot",
        url=url,
        duration_ms=data.get("duration_ms"),
        file_path=data.get("file_path"),
    )
    return data
```

- [ ] **Step 5: Register in `__init__.py`**

Add imports at the top of `src/donna/skills/tools/__init__.py`:

```python
from donna.skills.tools.browser_extract_text import browser_extract_text
from donna.skills.tools.browser_screenshot import browser_screenshot
```

Add registrations inside `register_default_tools()`, after the `html_extract` registration:

```python
    registry.register("browser_extract_text", browser_extract_text)
    registry.register("browser_screenshot", browser_screenshot)
```

Add to `__all__`:

```python
    "browser_extract_text",
    "browser_screenshot",
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/tools/test_browser_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/donna/skills/tools/browser_extract_text.py src/donna/skills/tools/browser_screenshot.py src/donna/skills/tools/__init__.py tests/tools/test_browser_tools.py
git commit -m "feat(tools): add browser_extract_text and browser_screenshot tools"
```

---

### Task 4: GPU Config & QueueItem Extension

**Files:**
- Modify: `src/donna/llm/types.py`
- Modify: `config/llm_gateway.yaml`
- Test: `tests/llm/test_types.py` (existing)

- [ ] **Step 1: Write failing test**

Add to existing `tests/llm/test_types.py` (or create if it doesn't exist):

```python
"""Tests for GPU config and QueueItem.required_model."""

import pytest

from donna.llm.types import GatewayConfig, GpuConfig, QueueItem, load_gateway_config


def test_queue_item_has_required_model():
    import asyncio
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    item = QueueItem(
        prompt="test", model="test", max_tokens=100,
        json_mode=True, future=future, required_model="qwen2.5-vl:7b",
    )
    assert item.required_model == "qwen2.5-vl:7b"
    loop.close()


def test_queue_item_required_model_default_none():
    import asyncio
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    item = QueueItem(
        prompt="test", model="test", max_tokens=100,
        json_mode=True, future=future,
    )
    assert item.required_model is None
    loop.close()


def test_gpu_config_defaults():
    cfg = GpuConfig()
    assert cfg.home_model == "qwen2.5:32b-instruct-q6_K"
    assert cfg.swap_timeout_s == 120
    assert cfg.restore_home_delay_s == 30
    assert cfg.swaps_per_hour_warning == 4
    assert cfg.swap_wait_ms_warning == 60000
    assert cfg.swap_overhead_pct_warning == 25


def test_load_gateway_config_with_gpu(tmp_path):
    config_file = tmp_path / "llm_gateway.yaml"
    config_file.write_text("""
gpu:
  home_model: "qwen2.5:32b-instruct-q6_K"
  swap_timeout_s: 90
  restore_home_delay_s: 15
  alerts:
    swaps_per_hour_warning: 6
""")
    cfg = load_gateway_config(tmp_path)
    assert cfg.gpu.home_model == "qwen2.5:32b-instruct-q6_K"
    assert cfg.gpu.swap_timeout_s == 90
    assert cfg.gpu.restore_home_delay_s == 15
    assert cfg.gpu.swaps_per_hour_warning == 6
    assert cfg.gpu.swap_wait_ms_warning == 60000  # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/llm/test_types.py -v -k gpu`
Expected: FAIL — `GpuConfig` does not exist, `required_model` not on QueueItem

- [ ] **Step 3: Add GpuConfig and extend QueueItem and GatewayConfig in types.py**

Add `GpuConfig` dataclass after the `ChainState` class (around line 37):

```python
@dataclass
class GpuConfig:
    """GPU model management configuration."""

    home_model: str = "qwen2.5:32b-instruct-q6_K"
    swap_timeout_s: int = 120
    restore_home_delay_s: int = 30
    swaps_per_hour_warning: int = 4
    swap_wait_ms_warning: int = 60000
    swap_overhead_pct_warning: int = 25
```

Add `required_model` field to `QueueItem` after `allow_cloud`:

```python
    required_model: str | None = None
```

Add `gpu` field to `GatewayConfig`:

```python
    gpu: GpuConfig = field(default_factory=GpuConfig)
```

Update `load_gateway_config()` to parse the `gpu` section:

```python
    gpu_raw = raw.get("gpu", {})
    gpu_alerts = gpu_raw.get("alerts", {})
    gpu_config = GpuConfig(
        home_model=str(gpu_raw.get("home_model", "qwen2.5:32b-instruct-q6_K")),
        swap_timeout_s=int(gpu_raw.get("swap_timeout_s", 120)),
        restore_home_delay_s=int(gpu_raw.get("restore_home_delay_s", 30)),
        swaps_per_hour_warning=int(gpu_alerts.get("swaps_per_hour_warning", 4)),
        swap_wait_ms_warning=int(gpu_alerts.get("swap_wait_ms_warning", 60000)),
        swap_overhead_pct_warning=int(gpu_alerts.get("swap_overhead_pct_warning", 25)),
    )
```

Pass `gpu=gpu_config` to the `GatewayConfig(...)` constructor.

- [ ] **Step 4: Add gpu section to config/llm_gateway.yaml**

Append at the end:

```yaml
gpu:
  home_model: "qwen2.5:32b-instruct-q6_K"
  swap_timeout_s: 120
  restore_home_delay_s: 30
  alerts:
    swaps_per_hour_warning: 4
    swap_wait_ms_warning: 60000
    swap_overhead_pct_warning: 25
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/llm/test_types.py -v -k gpu`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/llm/types.py config/llm_gateway.yaml tests/llm/test_types.py
git commit -m "feat(llm): add GpuConfig, QueueItem.required_model, gpu gateway config"
```

---

### Task 5: OllamaProvider.list_running() and GpuTracker

**Files:**
- Modify: `src/donna/models/providers/ollama.py`
- Create: `src/donna/llm/gpu_tracker.py`
- Test: `tests/llm/test_gpu_tracker.py`

- [ ] **Step 1: Write failing test for list_running**

Add to tests (create `tests/models/test_ollama_provider.py` if needed or add to existing):

```python
@pytest.mark.asyncio
async def test_list_running_returns_model_names(mock_ollama_session):
    """list_running() should return names of currently loaded models."""
    provider = OllamaProvider()
    # Mock will return a response with models list
    with patch.object(provider, '_get_session') as mock_session_fn:
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "models": [
                {"name": "qwen2.5:32b-instruct-q6_K", "size": 20000000000}
            ]
        })
        mock_session.get = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_session_fn.return_value = mock_session

        result = await provider.list_running()
        assert result == ["qwen2.5:32b-instruct-q6_K"]
```

- [ ] **Step 2: Add list_running() to OllamaProvider**

Add after `list_models()` in `src/donna/models/providers/ollama.py`:

```python
    async def list_running(self) -> list[str]:
        """Return names of models currently loaded in GPU memory.

        Calls Ollama's /api/ps endpoint.
        """
        try:
            session = self._get_session()
            async with session.get(f"{self._base_url}/api/ps") as resp:
                resp.raise_for_status()
                data = await resp.json()
                return [m["name"] for m in data.get("models", [])]
        except (TimeoutError, aiohttp.ClientError):
            return []
```

- [ ] **Step 3: Write failing test for GpuTracker**

Create `tests/llm/test_gpu_tracker.py`:

```python
"""Tests for GpuTracker — GPU model state and swap metrics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from donna.llm.gpu_tracker import GpuTracker
from donna.llm.types import GpuConfig


def _make_tracker(home: str = "qwen2.5:32b-instruct-q6_K") -> GpuTracker:
    config = GpuConfig(home_model=home)
    return GpuTracker(config)


class TestGpuTracker:
    def test_initial_state(self):
        t = _make_tracker()
        assert t.loaded_model is None
        assert t.is_home is False

    def test_record_loaded(self):
        t = _make_tracker()
        t.record_loaded("qwen2.5:32b-instruct-q6_K")
        assert t.loaded_model == "qwen2.5:32b-instruct-q6_K"
        assert t.is_home is True

    def test_record_swap(self):
        t = _make_tracker()
        t.record_loaded("qwen2.5:32b-instruct-q6_K")
        t.record_swap_started("qwen2.5-vl:7b")
        t.record_swap_completed("qwen2.5-vl:7b", duration_ms=5000)
        assert t.loaded_model == "qwen2.5-vl:7b"
        assert t.is_home is False
        assert t.swaps_this_hour >= 1

    def test_swap_metrics(self):
        t = _make_tracker()
        t.record_swap_started("model-a")
        t.record_swap_completed("model-a", duration_ms=3000)
        t.record_swap_started("model-b")
        t.record_swap_completed("model-b", duration_ms=5000)
        metrics = t.get_metrics()
        assert metrics["swaps_this_hour"] == 2
        assert metrics["avg_swap_duration_ms_1h"] == 4000

    def test_should_alert_swap_rate(self):
        config = GpuConfig(swaps_per_hour_warning=2)
        t = GpuTracker(config)
        t.record_swap_started("a")
        t.record_swap_completed("a", duration_ms=1000)
        assert t.check_alerts() == []
        t.record_swap_started("b")
        t.record_swap_completed("b", duration_ms=1000)
        t.record_swap_started("c")
        t.record_swap_completed("c", duration_ms=1000)
        alerts = t.check_alerts()
        assert any("swapped" in a.lower() for a in alerts)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/llm/test_gpu_tracker.py -v`
Expected: FAIL — module not found

- [ ] **Step 5: Create gpu_tracker.py**

```python
"""GpuTracker — tracks GPU model state, swap metrics, and alert thresholds."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

from donna.llm.types import GpuConfig

logger = structlog.get_logger()


@dataclass
class SwapRecord:
    """Record of a single model swap."""

    from_model: str | None
    to_model: str
    started_at: float
    duration_ms: int = 0
    completed: bool = False


class GpuTracker:
    """Tracks the currently loaded GPU model and rolling swap metrics.

    Not thread-safe — designed for single-worker access within LLMQueueWorker.
    """

    def __init__(self, config: GpuConfig) -> None:
        self._config = config
        self._loaded_model: str | None = None
        self._swaps: deque[SwapRecord] = deque(maxlen=200)
        self._current_swap: SwapRecord | None = None
        self._total_swap_ms_1h: int = 0
        self._total_exec_ms_1h: int = 0

    @property
    def loaded_model(self) -> str | None:
        return self._loaded_model

    @property
    def is_home(self) -> bool:
        return self._loaded_model == self._config.home_model

    @property
    def home_model(self) -> str:
        return self._config.home_model

    @property
    def swaps_this_hour(self) -> int:
        cutoff = time.monotonic() - 3600
        return sum(1 for s in self._swaps if s.completed and s.started_at > cutoff)

    def record_loaded(self, model: str) -> None:
        self._loaded_model = model

    def record_swap_started(self, to_model: str) -> None:
        self._current_swap = SwapRecord(
            from_model=self._loaded_model,
            to_model=to_model,
            started_at=time.monotonic(),
        )
        logger.info(
            "gpu_swap_started",
            from_model=self._loaded_model,
            to_model=to_model,
        )

    def record_swap_completed(self, to_model: str, duration_ms: int) -> None:
        if self._current_swap is not None:
            self._current_swap.duration_ms = duration_ms
            self._current_swap.completed = True
            self._swaps.append(self._current_swap)
            self._current_swap = None

        self._loaded_model = to_model
        self._total_swap_ms_1h += duration_ms

        logger.info(
            "gpu_swap_completed",
            to_model=to_model,
            duration_ms=duration_ms,
            swaps_this_hour=self.swaps_this_hour,
        )

    def record_execution_time(self, duration_ms: int) -> None:
        self._total_exec_ms_1h += duration_ms

    def get_metrics(self) -> dict[str, Any]:
        cutoff = time.monotonic() - 3600
        recent = [s for s in self._swaps if s.completed and s.started_at > cutoff]
        swap_count = len(recent)

        avg_swap_ms = 0
        if recent:
            avg_swap_ms = sum(s.duration_ms for s in recent) // len(recent)

        last_swap_ms = recent[-1].duration_ms if recent else 0

        total_time = self._total_swap_ms_1h + self._total_exec_ms_1h
        overhead_pct = (
            round(self._total_swap_ms_1h / total_time * 100, 1) if total_time > 0 else 0.0
        )

        return {
            "loaded_model": self._loaded_model,
            "is_home": self.is_home,
            "swaps_this_hour": swap_count,
            "last_swap_duration_ms": last_swap_ms,
            "avg_swap_duration_ms_1h": avg_swap_ms,
            "swap_overhead_pct_1h": overhead_pct,
        }

    def check_alerts(self) -> list[str]:
        alerts: list[str] = []
        metrics = self.get_metrics()

        if metrics["swaps_this_hour"] > self._config.swaps_per_hour_warning:
            alerts.append(
                f"GPU swapped {metrics['swaps_this_hour']} times in the last hour. "
                "Consider consolidating automation schedules."
            )

        if metrics["last_swap_duration_ms"] > self._config.swap_wait_ms_warning:
            secs = metrics["last_swap_duration_ms"] / 1000
            alerts.append(
                f"Last GPU swap took {secs:.0f}s. Model loading is slow — check Ollama health."
            )

        if metrics["swap_overhead_pct_1h"] > self._config.swap_overhead_pct_warning:
            alerts.append(
                f"{metrics['swap_overhead_pct_1h']}% of queue time spent loading models. "
                "Review model affinity groupings."
            )

        return alerts
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/llm/test_gpu_tracker.py tests/models/test_ollama_provider.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/donna/models/providers/ollama.py src/donna/llm/gpu_tracker.py tests/llm/test_gpu_tracker.py
git commit -m "feat(llm): add GpuTracker and OllamaProvider.list_running()"
```

---

### Task 6: GPU-Aware Queue Worker

**Files:**
- Modify: `src/donna/llm/queue.py`
- Test: `tests/llm/test_queue_gpu.py`

This is the core task. The worker gains model-affinity sorting in `_pop_next()`, model swap coordination in `_execute()`, and home model auto-restore.

- [ ] **Step 1: Write failing test**

Create `tests/llm/test_queue_gpu.py`:

```python
"""Tests for GPU-aware queue behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.llm.gpu_tracker import GpuTracker
from donna.llm.queue import LLMQueueWorker
from donna.llm.types import GatewayConfig, GpuConfig, Priority, QueueItem


def _make_worker(
    gpu_home: str = "qwen2.5:32b-instruct-q6_K",
) -> LLMQueueWorker:
    gpu_config = GpuConfig(home_model=gpu_home, restore_home_delay_s=0)
    config = GatewayConfig(gpu=gpu_config)
    ollama = AsyncMock()
    ollama.complete = AsyncMock(return_value=({"result": "ok"}, MagicMock(
        latency_ms=100, tokens_in=10, tokens_out=20, cost_usd=0.0,
    )))
    ollama.list_running = AsyncMock(return_value=["qwen2.5:32b-instruct-q6_K"])
    alerter = AsyncMock()
    rate_limiter = MagicMock()
    rate_limiter.get_all_usage = MagicMock(return_value={})

    worker = LLMQueueWorker(
        config=config, ollama=ollama,
        inv_logger=MagicMock(), alerter=alerter,
        rate_limiter=rate_limiter,
    )
    return worker


class TestModelAffinitySort:
    def test_pop_next_prefers_matching_model(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")

        loop = asyncio.new_event_loop()

        # Enqueue two internal items with different required_models
        f1 = loop.create_future()
        item_vision = QueueItem(
            prompt="vision", model="ollama", max_tokens=100,
            json_mode=True, future=f1, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5-vl:7b",
            sequence=1,
        )
        f2 = loop.create_future()
        item_text = QueueItem(
            prompt="text", model="ollama", max_tokens=100,
            json_mode=True, future=f2, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5:32b-instruct-q6_K",
            sequence=2,
        )

        worker._internal.put_nowait(item_vision)
        worker._internal.put_nowait(item_text)

        popped = worker._pop_next()
        assert popped is not None
        assert popped.required_model == "qwen2.5:32b-instruct-q6_K"

        loop.close()

    def test_pop_next_respects_priority_over_affinity(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")

        loop = asyncio.new_event_loop()

        f1 = loop.create_future()
        item_critical = QueueItem(
            prompt="critical", model="ollama", max_tokens=100,
            json_mode=True, future=f1, is_internal=True,
            priority=Priority.CRITICAL, required_model="qwen2.5-vl:7b",
            sequence=1,
        )
        f2 = loop.create_future()
        item_normal = QueueItem(
            prompt="normal", model="ollama", max_tokens=100,
            json_mode=True, future=f2, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5:32b-instruct-q6_K",
            sequence=2,
        )

        worker._internal.put_nowait(item_critical)
        worker._internal.put_nowait(item_normal)

        popped = worker._pop_next()
        assert popped is not None
        assert popped.priority == Priority.CRITICAL

        loop.close()


class TestGpuStatus:
    def test_status_includes_gpu_section(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")
        status = worker.get_status()
        assert "gpu" in status
        assert status["gpu"]["loaded_model"] == "qwen2.5:32b-instruct-q6_K"
        assert status["gpu"]["is_home"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/llm/test_queue_gpu.py -v`
Expected: FAIL — no `_gpu_tracker` attribute

- [ ] **Step 3: Modify queue.py**

Changes to `LLMQueueWorker`:

**Import GpuTracker:**

```python
from donna.llm.gpu_tracker import GpuTracker
```

**In `__init__`, after existing fields, add:**

```python
        self._gpu_tracker = GpuTracker(config.gpu)
        self._home_restore_task: asyncio.Task[None] | None = None
```

**Replace `_pop_next()` with model-affinity version:**

```python
    def _pop_next(self) -> QueueItem | None:
        """Pop the next item with model-affinity sorting.

        1. Internal queue first (with affinity within same priority)
        2. External priority deque (interrupted/continuation items)
        3. External queue
        """
        if not self._internal.empty():
            return self._pop_internal_with_affinity()

        if self._external_priority:
            return self._external_priority.popleft()

        if not self._external.empty():
            return self._external.get_nowait()

        return None

    def _pop_internal_with_affinity(self) -> QueueItem:
        """Drain internal queue, pick best item respecting priority + model affinity."""
        items: list[QueueItem] = []
        while not self._internal.empty():
            items.append(self._internal.get_nowait())

        if len(items) == 1:
            return items[0]

        best_priority = items[0].priority
        same_priority = [it for it in items if it.priority == best_priority]
        rest = [it for it in items if it.priority != best_priority]

        loaded = self._gpu_tracker.loaded_model
        matching = [it for it in same_priority if it.required_model is None or it.required_model == loaded]
        non_matching = [it for it in same_priority if it.required_model is not None and it.required_model != loaded]

        if matching:
            chosen = matching[0]
            requeue = matching[1:] + non_matching + rest
        else:
            chosen = non_matching[0] if non_matching else same_priority[0]
            requeue = (non_matching[1:] if non_matching else same_priority[1:]) + rest

        for it in requeue:
            self._internal.put_nowait(it)

        return chosen
```

**Modify `_execute()` to handle model swaps:**

```python
    async def _execute(self, item: QueueItem) -> tuple[dict[str, Any], CompletionMetadata]:
        """Execute an LLM call, swapping models if needed."""
        if self._home_restore_task is not None:
            self._home_restore_task.cancel()
            self._home_restore_task = None

        required = item.required_model
        if required and required != self._gpu_tracker.loaded_model:
            await self._swap_model(required)

        start = time.monotonic()
        result, meta = await self._ollama.complete(
            prompt=item.prompt,
            model=item.model,
            max_tokens=item.max_tokens,
            json_mode=item.json_mode,
        )
        exec_ms = int((time.monotonic() - start) * 1000)
        self._gpu_tracker.record_execution_time(exec_ms)

        self._schedule_home_restore_if_needed()

        return result, meta

    async def _swap_model(self, target_model: str) -> None:
        """Swap the loaded GPU model by sending requests to Ollama."""
        import asyncio as _asyncio

        self._gpu_tracker.record_swap_started(target_model)
        start = time.monotonic()

        current = self._gpu_tracker.loaded_model
        if current:
            try:
                await self._ollama.complete(
                    prompt="unload", model=current,
                    max_tokens=1, json_mode=False,
                )
            except Exception:
                pass

        try:
            await _asyncio.wait_for(
                self._ollama.complete(
                    prompt="warmup", model=target_model,
                    max_tokens=1, json_mode=False,
                ),
                timeout=self._config.gpu.swap_timeout_s,
            )
        except TimeoutError:
            logger.error("gpu_swap_timeout", target=target_model)

        running = await self._ollama.list_running()
        if running:
            self._gpu_tracker.record_loaded(running[0])

        duration_ms = int((time.monotonic() - start) * 1000)
        self._gpu_tracker.record_swap_completed(target_model, duration_ms)

        alerts = self._gpu_tracker.check_alerts()
        for alert_msg in alerts:
            if self._alerter:
                await self._alerter.send_alert("gpu_swap", alert_msg)

        async with self.state_changed:
            self.state_changed.notify_all()

    def _schedule_home_restore_if_needed(self) -> None:
        """Schedule restoring the home model after a delay if queue has no more non-home work."""
        if self._gpu_tracker.is_home:
            return

        has_non_home = False
        items: list[QueueItem] = []
        while not self._internal.empty():
            it = self._internal.get_nowait()
            items.append(it)
            if it.required_model and it.required_model != self._gpu_tracker.home_model:
                has_non_home = True
        for it in items:
            self._internal.put_nowait(it)

        if has_non_home:
            return

        delay = self._config.gpu.restore_home_delay_s
        self._home_restore_task = asyncio.create_task(self._restore_home(delay))

    async def _restore_home(self, delay_s: int) -> None:
        """Wait then swap back to the home model."""
        await asyncio.sleep(delay_s)
        if not self._gpu_tracker.is_home:
            await self._swap_model(self._gpu_tracker.home_model)
```

Add `import time` if not already present.

**Update `get_status()` to include GPU section:**

Add at the end of the returned dict:

```python
            "gpu": self._gpu_tracker.get_metrics(),
```

**Update `reload_config()` to refresh GPU config:**

```python
        self._gpu_tracker._config = config.gpu
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/llm/test_queue_gpu.py -v`
Expected: PASS

- [ ] **Step 5: Run full queue test suite**

Run: `pytest tests/llm/ -v`
Expected: All PASS (existing tests unaffected)

- [ ] **Step 6: Commit**

```bash
git add src/donna/llm/queue.py tests/llm/test_queue_gpu.py
git commit -m "feat(llm): GPU-aware queue with model-affinity sorting, swap coordination, and home restore"
```

---

### Task 7: Alembic Migration & Automation Model

**Files:**
- Create: `alembic/versions/xxxx_add_gpu_model_fields.py`
- Modify: `src/donna/automations/models.py`

- [ ] **Step 1: Create migration**

Run: `alembic revision --autogenerate -m "add gpu_model and preferred_window to automations"`

Then edit the generated file:

```python
"""add gpu_model and preferred_window to automations

Revision ID: <auto>
"""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("automations", sa.Column("gpu_model", sa.Text(), nullable=True))
    op.add_column("automations", sa.Column("preferred_window", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("automations", "preferred_window")
    op.drop_column("automations", "gpu_model")
```

- [ ] **Step 2: Update AutomationRow in models.py**

Add fields to the `AutomationRow` dataclass after `active_cadence_cron`:

```python
    gpu_model: str | None = None
    preferred_window: str | None = None
```

Update `AUTOMATION_COLUMNS` to include the new columns:

```python
AUTOMATION_COLUMNS = (
    "id", "user_id", "name", "description", "capability_name",
    "inputs", "trigger_type", "schedule", "alert_conditions",
    "alert_channels", "max_cost_per_run_usd", "min_interval_seconds",
    "status", "last_run_at", "next_run_at", "run_count",
    "failure_count", "created_at", "updated_at", "created_via",
    "active_cadence_cron", "gpu_model", "preferred_window",
)
```

Update `row_to_automation()` to parse the new columns:

```python
        active_cadence_cron=row[20] if len(row) > 20 else None,
        gpu_model=row[21] if len(row) > 21 else None,
        preferred_window=row[22] if len(row) > 22 else None,
```

- [ ] **Step 3: Run migration**

Run: `alembic upgrade head`
Expected: Migration applies cleanly

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/*add_gpu_model* src/donna/automations/models.py
git commit -m "feat(automations): add gpu_model and preferred_window columns"
```

---

### Task 8: Scheduler Model-Affinity Grouping

**Files:**
- Modify: `src/donna/automations/scheduler.py`
- Test: `tests/automations/test_scheduler_affinity.py`

- [ ] **Step 1: Write failing test**

Create `tests/automations/test_scheduler_affinity.py`:

```python
"""Tests for scheduler model-affinity grouping."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.scheduler import AutomationScheduler


def _make_row(aid: str, gpu_model: str | None = None) -> MagicMock:
    row = MagicMock()
    row.id = aid
    row.gpu_model = gpu_model
    return row


@pytest.mark.asyncio
async def test_groups_by_gpu_model_home_first():
    """Due automations are grouped: home model first, then each non-home group."""
    repo = AsyncMock()
    dispatcher = AsyncMock()

    rows = [
        _make_row("a1", gpu_model="qwen2.5-vl:7b"),
        _make_row("a2", gpu_model=None),  # no gpu_model = home
        _make_row("a3", gpu_model="qwen2.5-vl:7b"),
        _make_row("a4", gpu_model=None),
    ]
    repo.list_due = AsyncMock(return_value=rows)

    sched = AutomationScheduler(
        repository=repo,
        dispatcher=dispatcher,
        poll_interval_seconds=60,
        gpu_home_model="qwen2.5:32b-instruct-q6_K",
    )
    await sched.run_once()

    dispatched_ids = [call.args[0].id for call in dispatcher.dispatch.call_args_list]
    # Home-model items first (a2, a4), then vision items (a1, a3)
    assert dispatched_ids == ["a2", "a4", "a1", "a3"]


@pytest.mark.asyncio
async def test_no_gpu_model_dispatches_all():
    """When no rows have gpu_model, dispatch in original order."""
    repo = AsyncMock()
    dispatcher = AsyncMock()

    rows = [_make_row("a1"), _make_row("a2"), _make_row("a3")]
    repo.list_due = AsyncMock(return_value=rows)

    sched = AutomationScheduler(
        repository=repo,
        dispatcher=dispatcher,
        poll_interval_seconds=60,
    )
    await sched.run_once()

    dispatched_ids = [call.args[0].id for call in dispatcher.dispatch.call_args_list]
    assert dispatched_ids == ["a1", "a2", "a3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/automations/test_scheduler_affinity.py -v`
Expected: FAIL — `gpu_home_model` not accepted

- [ ] **Step 3: Modify scheduler.py**

Update `__init__` signature:

```python
    def __init__(
        self,
        *,
        repository: Any,
        dispatcher: Any,
        poll_interval_seconds: int,
        gpu_home_model: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repository
        self._dispatcher = dispatcher
        self._poll = poll_interval_seconds
        self._gpu_home_model = gpu_home_model
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False
        self._dispatching: set[str] = set()
```

Replace `run_once()`:

```python
    async def run_once(self) -> None:
        now = self._now_fn()
        try:
            due = await self._repo.list_due(now)
        except Exception:
            logger.exception("automation_scheduler_list_due_failed")
            return

        ordered = self._group_by_gpu_model(due)

        for row in ordered:
            aid: str | None = getattr(row, "id", None)
            if aid is None or aid in self._dispatching:
                continue
            self._dispatching.add(aid)
            try:
                await self._dispatcher.dispatch(row)
            except Exception:
                logger.exception(
                    "automation_scheduler_dispatch_failed",
                    automation_id=aid,
                )
            finally:
                self._dispatching.discard(aid)

    def _group_by_gpu_model(self, rows: list[Any]) -> list[Any]:
        """Reorder rows: home-model first, then each non-home group.

        Within each group, original order is preserved.
        """
        if not self._gpu_home_model:
            return rows

        home: list[Any] = []
        groups: dict[str, list[Any]] = {}

        for row in rows:
            gpu = getattr(row, "gpu_model", None)
            if gpu is None or gpu == self._gpu_home_model:
                home.append(row)
            else:
                groups.setdefault(gpu, []).append(row)

        result = list(home)
        for group in groups.values():
            result.extend(group)
        return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/automations/test_scheduler_affinity.py -v`
Expected: PASS

- [ ] **Step 5: Run full automation tests**

Run: `pytest tests/automations/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/automations/scheduler.py tests/automations/test_scheduler_affinity.py
git commit -m "feat(scheduler): model-affinity grouping — home model first, then non-home groups"
```

---

### Task 9: Conditional Step Support in Skill Executor

**Files:**
- Modify: `src/donna/skills/executor.py`
- Test: `tests/skills/test_executor_conditions.py`

- [ ] **Step 1: Write failing test**

Create `tests/skills/test_executor_conditions.py`:

```python
"""Tests for conditional step execution in SkillExecutor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.models import SkillRow, SkillVersionRow


def _make_skill() -> SkillRow:
    return SkillRow(
        id="test-skill", user_id="test", capability_name="test_cap",
        description="test", status="active",
        created_at=MagicMock(), updated_at=MagicMock(),
    )


def _make_version(yaml_backbone: str, step_content: dict | None = None) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1", skill_id="test-skill", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content or {},
        output_schemas={}, status="active",
        created_at=MagicMock(), updated_at=MagicMock(),
    )


@pytest.mark.asyncio
async def test_condition_true_runs_step():
    """Steps with truthy conditions should execute."""
    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"result": "ok"}, MagicMock(
        invocation_id="inv1", cost_usd=0.0,
    )))

    executor = SkillExecutor(model_router=router)
    yaml_bb = """
steps:
  - name: step_a
    kind: llm
    prompt: steps/a.md
  - name: step_b
    kind: llm
    prompt: steps/b.md
    condition: "state.step_a.result == 'ok'"
"""
    version = _make_version(yaml_bb, {"step_a": "prompt a", "step_b": "prompt b"})
    skill = _make_skill()

    result = await executor.execute(skill, version, {}, "user1")
    assert result.status == "succeeded"
    assert "step_b" in result.state


@pytest.mark.asyncio
async def test_condition_false_skips_step():
    """Steps with falsy conditions should be skipped."""
    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"result": "ok"}, MagicMock(
        invocation_id="inv1", cost_usd=0.0,
    )))

    executor = SkillExecutor(model_router=router)
    yaml_bb = """
steps:
  - name: step_a
    kind: llm
    prompt: steps/a.md
  - name: step_b
    kind: llm
    prompt: steps/b.md
    condition: "state.step_a.result == 'fail'"
"""
    version = _make_version(yaml_bb, {"step_a": "prompt a", "step_b": "prompt b"})
    skill = _make_skill()

    result = await executor.execute(skill, version, {}, "user1")
    assert result.status == "succeeded"
    assert "step_b" not in result.state


@pytest.mark.asyncio
async def test_on_failure_continue_sets_success_false():
    """Steps with on_failure=continue that fail should set success=false in state."""
    router = AsyncMock()
    call_count = 0

    async def mock_complete(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("LLM failed")
        return ({"result": "fallback"}, MagicMock(invocation_id="inv2", cost_usd=0.0))

    router.complete = mock_complete

    executor = SkillExecutor(model_router=router)
    yaml_bb = """
steps:
  - name: primary
    kind: llm
    prompt: steps/primary.md
    on_failure: continue
  - name: fallback
    kind: llm
    prompt: steps/fallback.md
    condition: "not state.primary.success"
"""
    version = _make_version(yaml_bb, {"primary": "primary prompt", "fallback": "fallback prompt"})
    skill = _make_skill()

    result = await executor.execute(skill, version, {}, "user1")
    assert result.status == "succeeded"
    assert result.state["primary"]["success"] is False
    assert "fallback" in result.state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/skills/test_executor_conditions.py -v`
Expected: FAIL — `condition` not evaluated, `on_failure: continue` not handled at step level

- [ ] **Step 3: Modify executor.py**

In the `execute()` method, at the top of the `while idx < len(steps):` loop (after extracting step metadata around line 199), add condition evaluation:

```python
            # Evaluate condition if present
            condition = step.get("condition")
            if condition:
                try:
                    cond_result = self._jinja.compile_expression(condition)(
                        state=state.to_dict(), inputs=inputs,
                    )
                except Exception:
                    cond_result = False

                if not cond_result:
                    logger.info(
                        "skill_step_skipped_condition",
                        skill_id=skill.id,
                        step_name=step_name,
                        condition=condition,
                    )
                    idx += 1
                    continue
```

Add `on_failure: continue` handling for LLM steps. In the `except` block that catches `SchemaValidationError, ToolInvocationError, DSLError, jinja2.UndefinedError` (around line 371), add a check before triage:

```python
                # Check step-level on_failure=continue
                step_on_failure = step.get("on_failure")
                if step_on_failure == "continue":
                    state[step_name] = {"success": False, "error": str(exc)}
                    record.validation_status = "continued"
                    step_results.append(record)
                    await self._persist_step_if_repo(skill_run_id, record)
                    logger.info(
                        "skill_step_continued",
                        skill_id=skill.id, step=step_name, error=str(exc),
                    )
                    idx += 1
                    prompt_additions = None
                    continue
```

Also, in the general `except Exception` block (around line 448), add the same check.

For successful steps, add `success: True` to state. After each successful step stores to state (the lines like `state[step_name] = llm_output` and `state[step_name] = collected`), wrap the output:

When storing LLM output into state, change:
```python
state[step_name] = llm_output
```
to:
```python
if isinstance(llm_output, dict):
    llm_output["success"] = True
state[step_name] = llm_output
```

For tool steps:
```python
if isinstance(collected, dict):
    collected["success"] = True
state[step_name] = collected
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/skills/test_executor_conditions.py -v`
Expected: PASS

- [ ] **Step 5: Run full executor test suite**

Run: `pytest tests/skills/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/executor.py tests/skills/test_executor_conditions.py
git commit -m "feat(skills): add conditional step execution and on_failure=continue for tier cascade"
```

---

### Task 10: Product Watch Skill Rewrite

**Files:**
- Modify: `skills/product_watch/skill.yaml`
- Modify: `skills/product_watch/steps/extract_product_info.md`
- Create: `skills/product_watch/steps/extract_from_screenshot.md`
- Create: `skills/product_watch/steps/extract_via_claude.md`
- Modify: `skills/product_watch/steps/format_output.md`

- [ ] **Step 1: Rewrite skill.yaml to multi-tier pipeline**

```yaml
capability_name: product_watch
version: 2
description: |
  Monitor a product URL for price and availability using a multi-tier
  extraction pipeline. Tier 1: Playwright text → local 32B. Tier 2:
  Playwright screenshot → local 7B vision. Tier 3: Claude tool_use
  fetches URL directly. Each tier cascades on failure.

inputs:
  schema_ref: capabilities/product_watch/input_schema.json

steps:
  - name: extract_text
    kind: tool
    tools: [browser_extract_text]
    tool_invocations:
      - tool: browser_extract_text
        args:
          url: "{{ inputs.url }}"
          selector: "{{ inputs.selector | default('body') }}"
        store_as: page_text

  - name: try_local_extract
    kind: llm
    prompt: steps/extract_product_info.md
    output_schema: schemas/extract_product_info_v1.json
    on_failure: continue

  - name: screenshot_fallback
    kind: tool
    tools: [browser_screenshot]
    condition: "not state.try_local_extract.success"
    tool_invocations:
      - tool: browser_screenshot
        args:
          url: "{{ inputs.url }}"
        store_as: screenshot

  - name: try_vision_extract
    kind: llm
    prompt: steps/extract_from_screenshot.md
    output_schema: schemas/extract_product_info_v1.json
    gpu_model: local_vision
    condition: "not state.try_local_extract.success"
    on_failure: continue

  - name: claude_fallback
    kind: llm
    prompt: steps/extract_via_claude.md
    output_schema: schemas/extract_product_info_v1.json
    model: parser
    tools: [web_fetch]
    condition: "not (state.try_local_extract.success or state.try_vision_extract.success)"

  - name: format_output
    kind: llm
    prompt: steps/format_output.md
    output_schema: schemas/format_output_v1.json

final_output: "{{ state.format_output }}"
```

- [ ] **Step 2: Update extract_product_info.md for Playwright text**

```markdown
You are extracting product information from the rendered text of a product page.

The text was extracted from the page using Playwright (innerText), not raw HTML.
It contains the visible text content only — no tags, attributes, or scripts.

Inputs:
- state.extract_text.page_text.text: the visible text content of the page
- state.extract_text.page_text.url: the URL that was fetched
- state.extract_text.page_text.selector_used: the CSS selector used

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the text shows no product information:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.

Page text:
{{ state.extract_text.page_text.text }}
```

- [ ] **Step 3: Create extract_from_screenshot.md**

```markdown
You are extracting product information from a screenshot of a product page.

A full-page screenshot has been captured. Analyze the visual content to
extract product details.

Screenshot path: {{ state.screenshot_fallback.screenshot.file_path }}
Page URL: {{ inputs.url }}

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the screenshot shows no product information:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.
```

- [ ] **Step 4: Create extract_via_claude.md**

```markdown
Extract product information from this URL: {{ inputs.url }}

Use the web_fetch tool to retrieve the page. Return structured data matching
this schema:

- price_usd: number — normalized to USD. Return null if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

Return ONLY the JSON object. No markdown, no explanation.
```

- [ ] **Step 5: Update format_output.md for multi-tier**

```markdown
You are computing the final output fields for a product_watch skill run.

Inputs (user-provided):
- inputs.url: the product URL that was monitored.
- inputs.max_price_usd: the maximum price (USD) above which NO alert should
  fire. Null = any price qualifies.
- inputs.required_size: the size the user wants. Null = any in-stock size
  qualifies.

Extracted info (from whichever tier succeeded):
{% if state.try_local_extract.success is defined and state.try_local_extract.success %}
- Tier: 1 (local text extraction)
- Extraction: state.try_local_extract
{% elif state.try_vision_extract is defined and state.try_vision_extract.success %}
- Tier: 2 (local vision extraction)
- Extraction: state.try_vision_extract
{% else %}
- Tier: 3 (Claude fallback)
- Extraction: state.claude_fallback
{% endif %}

{% set extraction = state.try_local_extract if (state.try_local_extract.success is defined and state.try_local_extract.success) else (state.try_vision_extract if (state.try_vision_extract is defined and state.try_vision_extract.success) else state.claude_fallback) %}

Compute the final output:
- ok: true
- price_usd: {{ extraction.price_usd }}
- currency: {{ extraction.currency }}
- in_stock: {{ extraction.in_stock }}
- size_available: true if inputs.required_size is null OR
                  inputs.required_size IN extraction.available_sizes.
                  Else false.
- triggers_alert: true if ALL of (in_stock, size_available,
                  (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd)).
                  Else false.
- title: {{ extraction.title }}
- tier: "tier_1_text" or "tier_2_vision" or "tier_3_claude" (whichever succeeded)

Return ONLY the JSON object.

Inputs: {{ inputs | tojson }}
Extraction data: {{ extraction | tojson }}
```

- [ ] **Step 6: Commit**

```bash
git add skills/product_watch/
git commit -m "feat(product_watch): rewrite to multi-tier extraction pipeline (text → vision → Claude)"
```

---

### Task 11: Tier Stats API Endpoint

**Files:**
- Modify: `src/donna/api/routes/automations.py`

- [ ] **Step 1: Add tier-stats endpoint**

Add to `src/donna/api/routes/automations.py`:

Request model:

```python
class TierStatsResponse(BaseModel):
    automation_id: str
    window_days: int
    total_runs: int
    tier_1_text: int
    tier_2_vision: int
    tier_3_claude: int
    estimated_claude_cost_usd: float
```

Endpoint:

```python
@router.get("/admin/automations/{automation_id}/tier-stats")
async def get_tier_stats(
    request: Request,
    automation_id: str,
    window_days: int = Query(default=30, ge=1, le=365),
) -> TierStatsResponse:
    """Return aggregated tier success counts for an automation."""
    repo: AutomationRepository = request.app.state.automation_repo

    runs = await repo.list_runs(automation_id, limit=1000)

    from datetime import timedelta
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    recent = [r for r in runs if r.started_at >= cutoff]

    tier_1 = 0
    tier_2 = 0
    tier_3 = 0
    for run in recent:
        if run.output and isinstance(run.output, dict):
            tier = run.output.get("tier", "")
            if "tier_1" in tier:
                tier_1 += 1
            elif "tier_2" in tier:
                tier_2 += 1
            elif "tier_3" in tier:
                tier_3 += 1

    return TierStatsResponse(
        automation_id=automation_id,
        window_days=window_days,
        total_runs=len(recent),
        tier_1_text=tier_1,
        tier_2_vision=tier_2,
        tier_3_claude=tier_3,
        estimated_claude_cost_usd=round(tier_3 * 0.03, 2),
    )
```

- [ ] **Step 2: Add gpu_model and preferred_window to request schemas**

Update `CreateAutomationRequest`:

```python
    gpu_model: str | None = None
    preferred_window: str | None = None
```

Update `UpdateAutomationRequest`:

```python
    gpu_model: str | None = None
    preferred_window: str | None = None
```

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/routes/automations.py
git commit -m "feat(api): add tier-stats endpoint and gpu_model fields to automation requests"
```

---

### Task 12: Dashboard — Tier Pills, GPU Card, Gallery Link

**Files:**
- Modify: `donna-ui/src/pages/SkillSystem/` (run history table)
- Modify: `donna-ui/src/api/skillSystem.ts` (tier stats API)

- [ ] **Step 1: Add tier pill to run history**

In the automation run history table component, add a column that renders a `Pill` for each run's tier:

```tsx
import { Pill } from "../../primitives/Pill";

function tierPill(tier: string | undefined) {
  if (!tier) return null;
  if (tier.includes("tier_1")) return <Pill variant="success">Tier 1</Pill>;
  if (tier.includes("tier_2")) return <Pill variant="warning">Tier 2</Pill>;
  if (tier.includes("tier_3")) return <Pill variant="error">Tier 3</Pill>;
  return null;
}
```

Add as a column in the run history table definition.

- [ ] **Step 2: Add GPU status card**

Create a small card component that polls `/llm/queue/status` and displays the `gpu` section:

```tsx
function GpuStatusCard({ gpu }: { gpu: GpuMetrics | null }) {
  if (!gpu) return null;
  return (
    <div className={styles.gpuCard}>
      <h4>GPU Status</h4>
      <div className={styles.gpuRow}>
        <span>Model:</span>
        <span>{gpu.loaded_model || "none"}</span>
      </div>
      <div className={styles.gpuRow}>
        <span>Status:</span>
        <Pill variant={gpu.is_home ? "success" : "warning"}>
          {gpu.is_home ? "Home" : "Away"}
        </Pill>
      </div>
      <div className={styles.gpuRow}>
        <span>Swaps/hr:</span>
        <span>{gpu.swaps_this_hour}</span>
      </div>
      <div className={styles.gpuRow}>
        <span>Overhead:</span>
        <span>{gpu.swap_overhead_pct_1h}%</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add gallery link to automation detail**

In the automation detail view, add a link:

```tsx
{automation.capability_name === "product_watch" && (
  <a
    href={`/browser/gallery?url=${encodeURIComponent(automation.inputs?.url || "")}`}
    target="_blank"
    rel="noopener noreferrer"
    className={styles.galleryLink}
  >
    View extraction gallery
  </a>
)}
```

- [ ] **Step 4: Add tier stats API call**

In `donna-ui/src/api/skillSystem.ts`:

```typescript
export interface TierStats {
  automation_id: string;
  window_days: number;
  total_runs: number;
  tier_1_text: number;
  tier_2_vision: number;
  tier_3_claude: number;
  estimated_claude_cost_usd: number;
}

export async function fetchTierStats(
  automationId: string,
  windowDays = 30,
): Promise<TierStats> {
  const { data } = await client.get(
    `/admin/automations/${automationId}/tier-stats`,
    { params: { window_days: windowDays } },
  );
  return data;
}
```

- [ ] **Step 5: Verify in browser**

Run: `cd donna-ui && npm run dev`
Navigate to the SkillSystem page.
Check: tier pills display on run history rows, GPU card shows on the page, gallery link opens for product_watch automations.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/
git commit -m "feat(ui): add tier pills, GPU status card, and browser gallery link"
```
