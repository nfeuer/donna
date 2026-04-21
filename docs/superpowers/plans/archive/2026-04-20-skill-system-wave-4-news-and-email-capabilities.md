# Skill System Wave 4 — News + Email Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed two new capabilities (`news_check`, `email_triage`) end-to-end via the Wave 2 replication pattern, adding three new read-only skill-system tools (`rss_fetch`, `gmail_search`, `gmail_get_message`) and since-last-run semantics via a dispatcher-injected `prior_run_end` input, without any schema changes.

**Architecture:** Additive replication of Wave 2. New tools are thin I/O shims registered into `DEFAULT_TOOL_REGISTRY` at startup; Gmail tools are conditionally registered when a `GmailClient` is available. Dispatcher queries the most recent successful `automation_run.end_time` and injects it as `prior_run_end` into skill inputs; skills use tool-native filters (`after:<ts>` for Gmail, `published > ts` for RSS) to return only items newer than the last run. Skill outputs conform to the existing digest shape `{ok, triggers_alert, message, meta}`; `NotificationService` is untouched. Zero migrations beyond capability seed rows.

**Tech Stack:** Python 3.12 async, aiosqlite, SQLAlchemy + Alembic, feedparser (new), existing `GmailClient`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-20-skill-system-wave-4-news-and-email-capabilities-design.md`

---

## File Structure

### Created files

- `src/donna/skills/tools/rss_fetch.py` — RSS/Atom parser tool.
- `src/donna/skills/tools/gmail_search.py` — Gmail search wrapper tool.
- `src/donna/skills/tools/gmail_get_message.py` — Gmail message-body fetcher tool.
- `skills/news_check/skill.yaml` — news_check skill backbone.
- `skills/news_check/steps/classify_items.md` — news_check LLM step 1 prompt.
- `skills/news_check/steps/render_digest.md` — news_check LLM step 2 prompt.
- `skills/news_check/schemas/classify_items_v1.json` — step output schema.
- `skills/news_check/schemas/render_digest_v1.json` — final output schema.
- `capabilities/news_check/input_schema.json` — capability input schema.
- `skills/news_check/fixtures/{news_with_new_items,news_no_new_items,news_empty_feed,news_feed_unreachable}.json` — 4 fixtures.
- `skills/email_triage/skill.yaml` — email_triage skill backbone.
- `skills/email_triage/steps/classify_snippets.md` — step 1 prompt.
- `skills/email_triage/steps/classify_bodies.md` — step 2 prompt.
- `skills/email_triage/steps/render_digest.md` — step 3 prompt.
- `skills/email_triage/schemas/{classify_snippets_v1,classify_bodies_v1,render_digest_v1}.json` — 3 output schemas.
- `capabilities/email_triage/input_schema.json` — capability input schema.
- `skills/email_triage/fixtures/{email_two_action_required,email_none_action_required,email_zero_matches,email_gmail_error}.json` — 4 fixtures.
- `alembic/versions/f3a4b5c6d7e8_seed_news_check_and_email_triage.py` — seed migration.
- `tests/unit/test_rss_fetch_tool.py` — rss_fetch unit tests.
- `tests/unit/test_gmail_tools.py` — gmail_search + gmail_get_message unit tests.
- `tests/unit/test_register_default_tools.py` — registry wiring tests.
- `tests/unit/test_dispatcher_prior_run_end.py` — dispatcher injection tests.
- `tests/unit/test_creation_path_capability_guard.py` — guard tests.
- `tests/e2e/test_wave4_news_check.py` — news E2E.
- `tests/e2e/test_wave4_email_triage.py` — email E2E.
- `tests/e2e/test_wave4_full_stack.py` — cross-capability integration.

### Modified files

- `pyproject.toml` — add feedparser dependency.
- `uv.lock` — regenerate via `uv lock`.
- `src/donna/skills/tools/__init__.py` — extend `register_default_tools` signature.
- `src/donna/cli_wiring.py` — thread `gmail_client` handle into `wire_skill_system`.
- `src/donna/automations/dispatcher.py` — inject `prior_run_end` in `_execute_skill` and in the claude_native `_build_prompt` path.
- `src/donna/automations/creation_flow.py` — capability-availability guard.
- `config/capabilities.yaml` — add news_check + email_triage entries.
- `docs/superpowers/followups/2026-04-16-skill-system-followups.md` — mark F-W3-A/B/C/D/F/G/H/I/J/K closed; add Wave 4 section.

---

## Task 1: Add feedparser dependency (W4-D1)

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add feedparser to pyproject.toml**

Open `pyproject.toml`. In the `[project]` table's `dependencies` array, add `"feedparser>=6.0.10,<7"` alphabetically (between existing entries). If the project uses PEP 621-style dependencies already, slot it in. If it uses poetry-style `[tool.poetry.dependencies]`, use `feedparser = ">=6.0.10,<7"` instead.

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Expected: Writes an updated `uv.lock` with a `[[package]] name = "feedparser"` entry. Fails if the project isn't using `uv` — fall back to the project's pinned tool (`poetry lock --no-update`, `pip-compile`, etc.) in that case.

- [ ] **Step 3: Verify the import works**

Run: `uv run python -c "import feedparser; print(feedparser.__version__)"`
Expected: prints `6.0.x` or higher.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(wave4): add feedparser >=6.0.10 for RSS/Atom parsing"
```

---

## Task 2: Write failing test for rss_fetch tool (W4-D2 part 1)

**Files:**
- Create: `tests/unit/test_rss_fetch_tool.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for donna.skills.tools.rss_fetch — RSS/Atom parsing + since filter."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from donna.skills.tools.rss_fetch import rss_fetch, RssFetchError


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<description>A feed</description>
<item>
  <title>Article One</title>
  <link>https://example.com/1</link>
  <pubDate>Mon, 20 Apr 2026 08:00:00 GMT</pubDate>
  <author>alice@example.com (Alice)</author>
  <description>First summary</description>
</item>
<item>
  <title>Article Two</title>
  <link>https://example.com/2</link>
  <pubDate>Sun, 19 Apr 2026 08:00:00 GMT</pubDate>
  <description>Older summary</description>
</item>
</channel></rss>
"""


@pytest.mark.asyncio
async def test_rss_fetch_parses_valid_rss_and_returns_items():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(url="https://example.com/feed")
    assert result["ok"] is True
    assert result["feed_title"] == "Test Feed"
    titles = [i["title"] for i in result["items"]]
    assert "Article One" in titles
    assert "Article Two" in titles


@pytest.mark.asyncio
async def test_rss_fetch_since_filter_drops_older_items():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(
            url="https://example.com/feed",
            since="2026-04-20T00:00:00+00:00",
        )
    titles = [i["title"] for i in result["items"]]
    assert titles == ["Article One"]


@pytest.mark.asyncio
async def test_rss_fetch_max_items_caps_result():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(url="https://example.com/feed", max_items=1)
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_rss_fetch_empty_feed_returns_empty_items():
    empty = """<?xml version="1.0"?><rss version="2.0"><channel>
    <title>Empty</title></channel></rss>"""
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=empty):
        result = await rss_fetch(url="https://example.com/feed")
    assert result["ok"] is True
    assert result["items"] == []


@pytest.mark.asyncio
async def test_rss_fetch_malformed_raises():
    with patch(
        "donna.skills.tools.rss_fetch._http_get",
        return_value="not xml at all",
    ):
        with pytest.raises(RssFetchError):
            await rss_fetch(url="https://example.com/feed")


@pytest.mark.asyncio
async def test_rss_fetch_atom_feed():
    atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Feed</title>
<entry>
  <title>Atom Article</title>
  <link href="https://example.com/atom/1"/>
  <updated>2026-04-20T10:00:00Z</updated>
  <summary>Atom summary</summary>
</entry>
</feed>
"""
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=atom):
        result = await rss_fetch(url="https://example.com/atom")
    titles = [i["title"] for i in result["items"]]
    assert "Atom Article" in titles
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_rss_fetch_tool.py -v`
Expected: All six tests fail with `ModuleNotFoundError: No module named 'donna.skills.tools.rss_fetch'`.

---

## Task 3: Implement rss_fetch tool (W4-D2 part 2)

**Files:**
- Create: `src/donna/skills/tools/rss_fetch.py`

- [ ] **Step 1: Write the implementation**

```python
"""rss_fetch — parse RSS/Atom URLs into structured items.

Thin async wrapper over `feedparser`. Offloads HTTP + parsing to a
thread. Normalizes output to a stable schema. Optional `since` (ISO-8601)
filters items server-side by published/updated timestamp.

Registered into DEFAULT_TOOL_REGISTRY at startup.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import struct_time, mktime
from typing import Any

import feedparser
import httpx
import structlog

logger = structlog.get_logger()


class RssFetchError(Exception):
    """Raised when rss_fetch cannot parse a response as RSS/Atom."""


async def _http_get(url: str, timeout_s: float) -> str:
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        return resp.text


def _parsed_time_to_iso(pt: struct_time | None) -> str | None:
    if pt is None:
        return None
    try:
        return datetime.fromtimestamp(mktime(pt), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _item_published_iso(entry: dict[str, Any]) -> str | None:
    # feedparser normalizes to *_parsed struct_time fields when present.
    for attr in ("published_parsed", "updated_parsed"):
        val = entry.get(attr)
        if val is not None:
            iso = _parsed_time_to_iso(val)
            if iso is not None:
                return iso
    return None


def _after(iso_a: str, iso_b: str) -> bool:
    return datetime.fromisoformat(iso_a) > datetime.fromisoformat(iso_b)


async def rss_fetch(
    url: str,
    since: str | None = None,
    max_items: int = 50,
    timeout_s: float = 10.0,
) -> dict:
    """Fetch + parse an RSS/Atom feed.

    Returns
    -------
    {
        "ok": True,
        "items": [{"title", "link", "published", "author", "summary"}, ...],
        "feed_title": str,
        "feed_description": str | None,
    }

    Raises
    ------
    RssFetchError — on unparseable / empty non-feed response.
    """
    try:
        body = await _http_get(url, timeout_s)
    except Exception as exc:
        logger.warning("rss_fetch_http_failed", url=url, error=str(exc))
        raise RssFetchError(f"http: {exc}") from exc

    parsed = await asyncio.to_thread(feedparser.parse, body)
    if parsed.bozo and not parsed.entries and not getattr(parsed.feed, "title", None):
        raise RssFetchError(f"unparseable feed at {url}: {parsed.bozo_exception!r}")

    feed_title = getattr(parsed.feed, "title", "")
    feed_desc = getattr(parsed.feed, "description", None)

    items: list[dict[str, Any]] = []
    for entry in parsed.entries[: max_items * 4]:  # over-read to survive since-filter
        published = _item_published_iso(entry)
        if since is not None and published is not None and not _after(published, since):
            continue
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": published,
            "author": entry.get("author", ""),
            "summary": entry.get("summary", ""),
        })
        if len(items) >= max_items:
            break

    return {
        "ok": True,
        "items": items,
        "feed_title": feed_title,
        "feed_description": feed_desc,
    }
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_rss_fetch_tool.py -v`
Expected: All six tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/donna/skills/tools/rss_fetch.py tests/unit/test_rss_fetch_tool.py
git commit -m "feat(skills): add rss_fetch tool with since-filter + atom support"
```

---

## Task 4: Write failing tests for Gmail tools (W4-D3 part 1)

**Files:**
- Create: `tests/unit/test_gmail_tools.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for gmail_search + gmail_get_message skill-system tools."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.tools.gmail_search import gmail_search, GmailToolError
from donna.skills.tools.gmail_get_message import gmail_get_message


class FakeEmailMessage:
    def __init__(self, *, id: str, sender: str, subject: str, snippet: str, date: datetime, body: str = ""):
        self.id = id
        self.sender = sender
        self.subject = subject
        self.snippet = snippet
        self.date = date
        self.recipients = ["nick@example.com"]
        self.body_text = body


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.search_emails = AsyncMock()
    c.get_message = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_gmail_search_returns_summaries(fake_client):
    fake_client.search_emails.return_value = [
        FakeEmailMessage(
            id="m1", sender="Jane <jane@x.com>", subject="Re: Q2",
            snippet="Let me know...", date=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    out = await gmail_search(
        client=fake_client, query="from:jane@x.com", max_results=5,
    )
    assert out["ok"] is True
    assert len(out["messages"]) == 1
    m = out["messages"][0]
    assert m["id"] == "m1"
    assert m["sender"].startswith("Jane")
    assert m["subject"] == "Re: Q2"
    assert m["snippet"].startswith("Let me know")
    assert m["internal_date"] == "2026-04-20T10:00:00+00:00"


@pytest.mark.asyncio
async def test_gmail_search_clamps_max_results(fake_client):
    fake_client.search_emails.return_value = []
    await gmail_search(client=fake_client, query="x", max_results=500)
    call_kwargs = fake_client.search_emails.call_args.kwargs
    assert call_kwargs["max_results"] == 100


@pytest.mark.asyncio
async def test_gmail_search_empty_query_raises(fake_client):
    with pytest.raises(GmailToolError):
        await gmail_search(client=fake_client, query="")


@pytest.mark.asyncio
async def test_gmail_search_propagates_client_failure(fake_client):
    fake_client.search_emails.side_effect = RuntimeError("token expired")
    with pytest.raises(GmailToolError):
        await gmail_search(client=fake_client, query="x")


@pytest.mark.asyncio
async def test_gmail_get_message_returns_body(fake_client):
    fake_client.get_message.return_value = FakeEmailMessage(
        id="m1", sender="Jane <jane@x.com>", subject="Re: Q2",
        snippet="Let me know...",
        date=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        body="Hey — need your roadmap thoughts by Friday.",
    )
    out = await gmail_get_message(client=fake_client, message_id="m1")
    assert out["ok"] is True
    assert out["sender"] == "Jane <jane@x.com>"
    assert out["subject"] == "Re: Q2"
    assert out["body_plain"].startswith("Hey")
    assert out["body_html"] is None


@pytest.mark.asyncio
async def test_gmail_tools_never_call_compose_or_send(fake_client):
    # Structural assertion: the wrappers must not reference these methods.
    fake_client.create_draft = AsyncMock()
    fake_client.send_draft = AsyncMock()
    fake_client.search_emails.return_value = []
    fake_client.get_message.return_value = FakeEmailMessage(
        id="m1", sender="x@y", subject="s", snippet="sn",
        date=datetime(2026, 4, 20, tzinfo=timezone.utc),
    )
    await gmail_search(client=fake_client, query="x")
    await gmail_get_message(client=fake_client, message_id="m1")
    fake_client.create_draft.assert_not_called()
    fake_client.send_draft.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_gmail_tools.py -v`
Expected: All six tests fail with `ModuleNotFoundError: No module named 'donna.skills.tools.gmail_search'`.

---

## Task 5: Implement gmail_search + gmail_get_message (W4-D3 part 2)

**Files:**
- Create: `src/donna/skills/tools/gmail_search.py`
- Create: `src/donna/skills/tools/gmail_get_message.py`

- [ ] **Step 1: Write gmail_search.py**

```python
"""gmail_search — thin read-only wrapper around GmailClient.search_emails.

Registered into DEFAULT_TOOL_REGISTRY as a bound callable with
``client`` partially applied when a GmailClient is available at boot.

Read-only by construction: this wrapper only ever reads from the
underlying GmailClient. It does not import or reference draft/send methods.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

MAX_RESULTS_CEILING = 100


class GmailToolError(Exception):
    """Raised when a Gmail tool cannot complete its call."""


async def gmail_search(
    *,
    client: Any,
    query: str,
    max_results: int = 20,
) -> dict:
    """Search Gmail. Returns lightweight summaries, never bodies."""
    if not query or not query.strip():
        raise GmailToolError("query must be non-empty")
    clamped = min(int(max_results), MAX_RESULTS_CEILING)
    try:
        messages = await client.search_emails(query=query, max_results=clamped)
    except Exception as exc:
        logger.warning("gmail_search_failed", query=query, error=str(exc))
        raise GmailToolError(f"search: {exc}") from exc

    out = []
    for m in messages:
        out.append({
            "id": m.id,
            "sender": m.sender,
            "subject": m.subject,
            "snippet": m.snippet,
            "internal_date": m.date.isoformat() if m.date is not None else None,
        })
    return {"ok": True, "messages": out}
```

- [ ] **Step 2: Write gmail_get_message.py**

```python
"""gmail_get_message — thin read-only wrapper around GmailClient.get_message.

Returns plain-text body preferentially; HTML body only when no plain
alternative exists. Read-only by construction.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.skills.tools.gmail_search import GmailToolError

logger = structlog.get_logger()


async def gmail_get_message(
    *,
    client: Any,
    message_id: str,
) -> dict:
    if not message_id or not message_id.strip():
        raise GmailToolError("message_id must be non-empty")
    try:
        m = await client.get_message(message_id=message_id)
    except Exception as exc:
        logger.warning("gmail_get_message_failed", id=message_id, error=str(exc))
        raise GmailToolError(f"get_message: {exc}") from exc

    body_plain = getattr(m, "body_text", "") or ""
    body_html = getattr(m, "body_html", None)
    if body_plain:
        body_html = None  # prefer plain
    return {
        "ok": True,
        "sender": m.sender,
        "subject": m.subject,
        "body_plain": body_plain,
        "body_html": body_html,
        "internal_date": m.date.isoformat() if m.date is not None else None,
        "headers": {"To": ", ".join(getattr(m, "recipients", []) or [])},
    }
```

- [ ] **Step 3: Verify `GmailClient` exposes a `get_message` method matching the wrapper's expectations**

Run: `uv run python -c "from donna.integrations.gmail import GmailClient; print([m for m in dir(GmailClient) if not m.startswith('_')])"`
Expected: list includes `search_emails` and `get_message` (or similar). If `get_message` is absent, add a thin `get_message` method to `GmailClient` delegating to the existing Gmail v1 `users().messages().get(...)` call — keep it single-concern and test-compatible with the fake in `tests/unit/test_gmail_tools.py`.

If you need to add the method, insert in `src/donna/integrations/gmail.py` after `search_emails`:

```python
async def get_message(self, *, message_id: str) -> EmailMessage:
    """Fetch a single message by id and return an EmailMessage."""
    if self._service is None:
        raise RuntimeError("GmailClient not authenticated")

    def _do_get() -> dict[str, Any]:
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    raw = await asyncio.to_thread(_do_get)
    return _raw_to_message(raw)
```

(Re-use whatever `_raw_to_message`/normalization helper is already used by `search_emails`. If none exists, extract one.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_gmail_tools.py -v`
Expected: All six tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/tools/gmail_search.py src/donna/skills/tools/gmail_get_message.py tests/unit/test_gmail_tools.py src/donna/integrations/gmail.py
git commit -m "feat(skills): add gmail_search + gmail_get_message tools (read-only)"
```

---

## Task 6: Write failing tests for extended register_default_tools (W4-D4 part 1)

**Files:**
- Create: `tests/unit/test_register_default_tools.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for the extended register_default_tools signature in Wave 4."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools import register_default_tools


def test_registers_web_fetch_and_rss_fetch_without_gmail_client():
    reg = ToolRegistry()
    register_default_tools(reg)
    names = set(reg.list_tool_names())
    assert "web_fetch" in names
    assert "rss_fetch" in names
    assert "gmail_search" not in names
    assert "gmail_get_message" not in names


def test_registers_gmail_tools_when_client_provided():
    reg = ToolRegistry()
    fake_client = MagicMock()
    register_default_tools(reg, gmail_client=fake_client)
    names = set(reg.list_tool_names())
    assert "gmail_search" in names
    assert "gmail_get_message" in names


@pytest.mark.asyncio
async def test_registered_gmail_search_binds_the_client():
    from unittest.mock import AsyncMock

    from datetime import datetime, timezone

    class _FakeMsg:
        id = "m1"; sender = "x@y"; subject = "s"; snippet = "sn"
        date = datetime(2026, 4, 20, tzinfo=timezone.utc)
        recipients = []

    fake = MagicMock()
    fake.search_emails = AsyncMock(return_value=[_FakeMsg()])
    reg = ToolRegistry()
    register_default_tools(reg, gmail_client=fake)

    out = await reg.dispatch(
        "gmail_search",
        {"query": "from:x@y"},
        allowed_tools=["gmail_search"],
    )
    assert out["ok"] is True
    assert len(out["messages"]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_register_default_tools.py -v`
Expected: Failures — either the new signature isn't present or the tools aren't registered.

---

## Task 7: Extend register_default_tools signature (W4-D4 part 2)

**Files:**
- Modify: `src/donna/skills/tools/__init__.py`

- [ ] **Step 1: Rewrite `src/donna/skills/tools/__init__.py`**

```python
"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup via
register_default_tools().
"""
from __future__ import annotations

from functools import partial
from typing import Any

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools.web_fetch import web_fetch
from donna.skills.tools.rss_fetch import rss_fetch
from donna.skills.tools.gmail_search import gmail_search
from donna.skills.tools.gmail_get_message import gmail_get_message


# Module-level registry populated at orchestrator startup via
# register_default_tools(DEFAULT_TOOL_REGISTRY). SkillExecutor instances
# that don't receive an explicit tool_registry default to this one.
DEFAULT_TOOL_REGISTRY: ToolRegistry = ToolRegistry()


def register_default_tools(
    registry: ToolRegistry,
    *,
    gmail_client: Any | None = None,
) -> None:
    """Register built-in skill tools.

    Always registers: web_fetch, rss_fetch.
    Registers gmail_search + gmail_get_message only when a GmailClient is
    provided (production wiring threads the existing integration handle;
    tests / degraded-mode boot pass None).
    """
    registry.register("web_fetch", web_fetch)
    registry.register("rss_fetch", rss_fetch)

    if gmail_client is not None:
        registry.register(
            "gmail_search",
            partial(gmail_search, client=gmail_client),
        )
        registry.register(
            "gmail_get_message",
            partial(gmail_get_message, client=gmail_client),
        )


__all__ = [
    "DEFAULT_TOOL_REGISTRY",
    "register_default_tools",
    "web_fetch",
    "rss_fetch",
    "gmail_search",
    "gmail_get_message",
]
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_register_default_tools.py -v`
Expected: All three tests pass.

- [ ] **Step 3: Run the existing tool-registry suite to confirm no regression**

Run: `uv run pytest tests/unit/test_skills_tool_registry.py tests/unit/test_skills_tools_web_fetch.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/donna/skills/tools/__init__.py tests/unit/test_register_default_tools.py
git commit -m "feat(skills): extend register_default_tools with optional gmail_client"
```

---

## Task 8: Thread GmailClient into wire_skill_system (W4-D4 part 3)

**Files:**
- Modify: `src/donna/cli_wiring.py`

- [ ] **Step 1: Locate where register_default_tools is called today**

Run: `uv run grep -n "register_default_tools" src/donna/cli_wiring.py src/donna/cli.py`
Expected: at least one callsite. Usually in `wire_skill_system` and/or a startup hook.

- [ ] **Step 2: Add a gmail_client parameter to wire_skill_system and thread it to register_default_tools**

In `src/donna/cli_wiring.py`, find the `wire_skill_system` function signature. Add a `gmail_client: Any | None = None` keyword argument. In its body, update the `register_default_tools(DEFAULT_TOOL_REGISTRY)` call to `register_default_tools(DEFAULT_TOOL_REGISTRY, gmail_client=gmail_client)`.

- [ ] **Step 3: Update the caller in `_run_orchestrator` (cli.py) or wherever wire_skill_system is invoked**

Find where `wire_skill_system(ctx)` is called. If `ctx` already has an `email` or `gmail` subsystem handle, pass its `.gmail_client` through. If not, the email subsystem wiring returns a handle that includes the client — pass `email_handle.gmail_client if email_handle else None`.

Example:

```python
skill_system_handle = wire_skill_system(
    ctx,
    gmail_client=email_handle.gmail_client if email_handle else None,
)
```

- [ ] **Step 4: Run the CLI wiring tests**

Run: `uv run pytest tests/unit/test_cli_wires_tools_and_capabilities.py tests/unit/test_startup_wiring_validation_factory.py -v`
Expected: all pass. If a test patched `register_default_tools` with the old signature, update it to accept `gmail_client=` as a kwarg.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli_wiring.py src/donna/cli.py tests/unit/test_cli_wires_tools_and_capabilities.py
git commit -m "feat(wiring): thread GmailClient through wire_skill_system"
```

---

## Task 9: Write failing test for dispatcher prior_run_end injection (W4-D5 part 1)

**Files:**
- Create: `tests/unit/test_dispatcher_prior_run_end.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Tests: AutomationDispatcher injects prior_run_end into skill inputs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


async def _make_empty_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        "CREATE TABLE automation_run (id TEXT PRIMARY KEY, automation_id TEXT, "
        "status TEXT, end_time TEXT)"
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_first_ever_run_injects_null_prior_run_end():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got is None
    await conn.close()


@pytest.mark.asyncio
async def test_second_run_returns_prior_end_time():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    prior = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, end_time) "
        "VALUES (?, ?, 'ok', ?)",
        (str(uuid.uuid4()), automation_id, prior),
    )
    await conn.commit()

    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn
    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got == prior
    await conn.close()


@pytest.mark.asyncio
async def test_failed_prior_run_ignored():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    ok_time = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    failed_time = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, end_time) "
        "VALUES (?, ?, 'ok', ?)",
        (str(uuid.uuid4()), automation_id, ok_time),
    )
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, end_time) "
        "VALUES (?, ?, 'failed', ?)",
        (str(uuid.uuid4()), automation_id, failed_time),
    )
    await conn.commit()

    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn
    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got == ok_time  # latest OK run, not latest-overall
    await conn.close()


@pytest.mark.asyncio
async def test_execute_skill_injects_prior_run_end_into_inputs():
    """Verify the call to executor.execute carries prior_run_end in inputs."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    prior = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, end_time) "
        "VALUES (?, ?, 'ok', ?)",
        (str(uuid.uuid4()), automation_id, prior),
    )
    await conn.commit()

    # Minimal Automation stub.
    automation = MagicMock()
    automation.id = automation_id
    automation.capability_name = "news_check"
    automation.inputs = {"url": "x"}
    automation.user_id = "u1"

    # Skill + version rows so _execute_skill's SELECTs don't fail.
    await conn.execute(
        "CREATE TABLE skill (id TEXT PRIMARY KEY, capability_name TEXT, "
        "current_version_id TEXT, state TEXT, requires_human_gate INT, "
        "baseline_agreement REAL, created_at TEXT, updated_at TEXT)"
    )
    await conn.execute(
        "CREATE TABLE skill_version (id TEXT PRIMARY KEY, skill_id TEXT, "
        "version_number INT, yaml_backbone TEXT, step_content TEXT, "
        "output_schemas TEXT, created_by TEXT, changelog TEXT, created_at TEXT)"
    )
    skill_id = str(uuid.uuid4()); vid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO skill VALUES (?, ?, ?, 'sandbox', 0, 0.0, ?, ?)",
        (skill_id, "news_check", vid, prior, prior),
    )
    await conn.execute(
        "INSERT INTO skill_version VALUES (?, ?, 1, 'yaml', '{}', '{}', 'seed', '', ?)",
        (vid, skill_id, prior),
    )
    await conn.commit()

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        final_output={"ok": True}, total_cost_usd=0.0, status="succeeded",
        run_id=None, error=None, escalation_reason=None,
    ))
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    await dispatcher._execute_skill(executor, automation, automation_run_id=None)

    call_kwargs = executor.execute.call_args.kwargs
    assert call_kwargs["inputs"]["prior_run_end"] == prior
    assert call_kwargs["inputs"]["url"] == "x"
    await conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_dispatcher_prior_run_end.py -v`
Expected: All four fail because `_query_prior_run_end` doesn't exist and `_execute_skill` doesn't inject `prior_run_end`.

---

## Task 10: Implement dispatcher prior_run_end injection (W4-D5 part 2)

**Files:**
- Modify: `src/donna/automations/dispatcher.py`

- [ ] **Step 1: Add `_query_prior_run_end` helper**

Insert after `_execute_skill` (around line 274):

```python
    async def _query_prior_run_end(self, *, automation_id: str) -> str | None:
        """Return the end_time of the most recent successful run, or None."""
        cursor = await self._conn.execute(
            "SELECT end_time FROM automation_run "
            "WHERE automation_id = ? AND status = 'ok' "
            "ORDER BY end_time DESC LIMIT 1",
            (automation_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row is not None else None
```

- [ ] **Step 2: Inject prior_run_end into `_execute_skill`'s executor call**

Modify the `executor.execute(...)` call (around line 268-274) to build inputs with `prior_run_end`:

```python
        prior_run_end = await self._query_prior_run_end(automation_id=automation.id)
        merged_inputs = dict(automation.inputs or {})
        merged_inputs["prior_run_end"] = prior_run_end

        return await executor.execute(
            skill=skill,
            version=version,
            capability_name=automation.capability_name,
            inputs=merged_inputs,
            user_id=automation.user_id,
            automation_run_id=automation_run_id,
        )
```

- [ ] **Step 3: Inject prior_run_end into the claude_native path's _build_prompt**

Modify `_build_prompt` (around line 288) to include prior_run_end in the prompt's inputs JSON. Since it takes only `automation`, thread in the value:

Replace the method with:

```python
    def _build_prompt(
        self,
        automation: AutomationRow,
        *,
        prior_run_end: str | None = None,
    ) -> str:
        inputs = dict(automation.inputs or {})
        inputs["prior_run_end"] = prior_run_end
        return (
            f"Execute capability '{automation.capability_name}' with the following inputs. "
            f"Return a strict JSON object matching the capability's output schema.\n\n"
            f"Inputs:\n{json.dumps(inputs, indent=2)}"
        )
```

And at the callsite in `dispatch` (line 111), query prior_run_end and pass it:

```python
                prior_run_end = await self._query_prior_run_end(automation_id=automation.id)
                parsed, metadata = await self._router.complete(
                    prompt=self._build_prompt(automation, prior_run_end=prior_run_end),
                    task_type=automation.capability_name,
                    task_id=None,
                    user_id=automation.user_id,
                )
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/unit/test_dispatcher_prior_run_end.py -v`
Expected: All four pass.

- [ ] **Step 5: Run the full dispatcher regression suite**

Run: `uv run pytest tests/unit/ -k dispatcher -v`
Expected: All pass. In particular, Wave 2 `product_watch` tests — their `automation.inputs` now carries an extra `prior_run_end: null` field, which should be inert.

Run: `uv run pytest tests/e2e/test_wave2_product_watch.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/automations/dispatcher.py tests/unit/test_dispatcher_prior_run_end.py
git commit -m "feat(automations): inject prior_run_end into skill inputs for since-last-run semantics"
```

---

## Task 11: Write news_check skill artifacts (W4-D6 part 1)

**Files:**
- Create: `skills/news_check/skill.yaml`
- Create: `skills/news_check/steps/classify_items.md`
- Create: `skills/news_check/steps/render_digest.md`
- Create: `skills/news_check/schemas/classify_items_v1.json`
- Create: `skills/news_check/schemas/render_digest_v1.json`
- Create: `capabilities/news_check/input_schema.json`

- [ ] **Step 1: Write skills/news_check/skill.yaml**

```yaml
capability_name: news_check
version: 1
description: |
  Monitor RSS/Atom feeds for new items matching user-specified topics.
  Since-last-run semantics via prior_run_end (tool filters server-side).
  Emits a digest DM when items newer than prior_run_end match any topic.

inputs:
  schema_ref: capabilities/news_check/input_schema.json

steps:
  - name: fetch_items
    kind: tool
    tools: [rss_fetch]
    tool_invocations:
      - tool: rss_fetch
        args:
          url: "{{ inputs.feed_urls[0] }}"
          since: "{{ inputs.prior_run_end }}"
          max_items: 50
        retry:
          max_attempts: 2
          backoff_s: [2, 5]
        store_as: feed

  - name: classify_items
    kind: llm
    prompt: steps/classify_items.md
    output_schema: schemas/classify_items_v1.json

  - name: render_digest
    kind: llm
    prompt: steps/render_digest.md
    output_schema: schemas/render_digest_v1.json

final_output: "{{ state.render_digest }}"
```

- [ ] **Step 2: Write skills/news_check/steps/classify_items.md**

```markdown
You are classifying RSS/Atom feed items for topic relevance.

**Inputs available in context:**
- `inputs.topics`: list of topic keywords the user cares about.
- `state.feed.items`: list of `{title, link, published, author, summary}` already filtered server-side to items published after `prior_run_end`.

**Your job:**
For each item in `state.feed.items`, decide if it materially matches ANY topic in `inputs.topics`. Material match = the title or summary is clearly ABOUT the topic, not just mentioning it.

**Return ONLY JSON matching the schema below.** No prose, no markdown fences.

Schema:
```
{
  "matches": [
    {"title": str, "link": str, "published": str|null, "summary_short": str, "matched_topics": [str]}
  ],
  "total_scanned": int,
  "total_matched": int
}
```

Produce `summary_short` as a single sentence (≤ 140 chars). If the feed item has no matching topic, omit it from `matches`.
```

- [ ] **Step 3: Write skills/news_check/schemas/classify_items_v1.json**

```json
{
  "type": "object",
  "required": ["matches", "total_scanned", "total_matched"],
  "properties": {
    "matches": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "link", "summary_short", "matched_topics"],
        "properties": {
          "title": {"type": "string"},
          "link": {"type": "string"},
          "published": {"type": ["string", "null"]},
          "summary_short": {"type": "string"},
          "matched_topics": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "total_scanned": {"type": "integer", "minimum": 0},
    "total_matched": {"type": "integer", "minimum": 0}
  }
}
```

- [ ] **Step 4: Write skills/news_check/steps/render_digest.md**

```markdown
You are rendering a digest DM summarizing new matching news items.

**Inputs available:**
- `state.classify_items.matches`: list of `{title, link, summary_short, matched_topics}`.
- `state.classify_items.total_scanned`: total items inspected.
- `state.feed.feed_title`: source feed title.
- `inputs.topics`: topic list.

**Your job:**
Return JSON matching the schema below. Keep the `message` under 1200 chars — if more than 5 matches, list the first 5 then append `"+<n> more."`. If zero matches, `triggers_alert=false` and `message=null`.

Be concise. Each line format: `• <title> — <link>`. No emojis.

Schema:
```
{
  "ok": true,
  "triggers_alert": bool,
  "message": string|null,
  "meta": {
    "item_count": int,
    "action_required_count": int,
    "source_feed": string
  }
}
```

Where `action_required_count` is the number of matched items (synonym for match count; keeps shape parity with email_triage).
```

- [ ] **Step 5: Write skills/news_check/schemas/render_digest_v1.json**

```json
{
  "type": "object",
  "required": ["ok", "triggers_alert", "message", "meta"],
  "properties": {
    "ok": {"type": "boolean"},
    "triggers_alert": {"type": "boolean"},
    "message": {"type": ["string", "null"]},
    "meta": {
      "type": "object",
      "required": ["item_count", "action_required_count", "source_feed"],
      "properties": {
        "item_count": {"type": "integer", "minimum": 0},
        "action_required_count": {"type": "integer", "minimum": 0},
        "source_feed": {"type": "string"}
      }
    }
  }
}
```

- [ ] **Step 6: Write capabilities/news_check/input_schema.json**

```json
{
  "type": "object",
  "required": ["feed_urls", "topics"],
  "properties": {
    "feed_urls": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 1,
      "description": "List of RSS/Atom feed URLs to monitor."
    },
    "topics": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 1,
      "description": "Topic keywords to match items against."
    },
    "prior_run_end": {
      "type": ["string", "null"],
      "description": "Injected by dispatcher; skill passes to rss_fetch.since."
    }
  }
}
```

- [ ] **Step 7: Commit the artifacts (migration wires them up later in Task 13)**

```bash
git add skills/news_check/ capabilities/news_check/
git commit -m "feat(skills): add news_check skill artifacts (yaml, prompts, schemas)"
```

---

## Task 12: Write news_check fixtures (W4-D7)

**Files:**
- Create: `skills/news_check/fixtures/news_with_new_items.json`
- Create: `skills/news_check/fixtures/news_no_new_items.json`
- Create: `skills/news_check/fixtures/news_empty_feed.json`
- Create: `skills/news_check/fixtures/news_feed_unreachable.json`

- [ ] **Step 1: Write news_with_new_items.json**

```json
{
  "case_name": "news_with_new_items",
  "input": {
    "feed_urls": ["https://example.com/ai-safety-feed"],
    "topics": ["AI safety", "alignment"],
    "prior_run_end": "2026-04-19T12:00:00+00:00"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert", "message", "meta"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [true]},
      "message": {"type": "string"},
      "meta": {
        "type": "object",
        "required": ["item_count", "action_required_count", "source_feed"],
        "properties": {
          "item_count": {"type": "integer", "minimum": 2},
          "action_required_count": {"type": "integer", "minimum": 2}
        }
      }
    }
  },
  "tool_mocks": {
    "rss_fetch:{\"url\":\"https://example.com/ai-safety-feed\",\"since\":\"2026-04-19T12:00:00+00:00\",\"max_items\":50}": {
      "ok": true,
      "feed_title": "AI Safety Daily",
      "feed_description": "Daily roundup",
      "items": [
        {
          "title": "New interpretability paper on alignment",
          "link": "https://example.com/a1",
          "published": "2026-04-20T08:00:00+00:00",
          "author": "alice@x.com",
          "summary": "A paper on scalable alignment interpretability."
        },
        {
          "title": "Policy brief: AI safety frameworks",
          "link": "https://example.com/a2",
          "published": "2026-04-20T06:00:00+00:00",
          "author": "bob@x.com",
          "summary": "Overview of current AI safety regulatory approaches."
        },
        {
          "title": "Unrelated gardening post",
          "link": "https://example.com/a3",
          "published": "2026-04-20T05:00:00+00:00",
          "author": "carol@x.com",
          "summary": "Tomatoes grow better with compost."
        }
      ]
    }
  }
}
```

- [ ] **Step 2: Write news_no_new_items.json**

```json
{
  "case_name": "news_no_new_items",
  "input": {
    "feed_urls": ["https://example.com/ai-safety-feed"],
    "topics": ["AI safety"],
    "prior_run_end": "2026-04-20T12:00:00+00:00"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert", "message", "meta"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [false]},
      "message": {"type": "null"}
    }
  },
  "tool_mocks": {
    "rss_fetch:{\"url\":\"https://example.com/ai-safety-feed\",\"since\":\"2026-04-20T12:00:00+00:00\",\"max_items\":50}": {
      "ok": true,
      "feed_title": "AI Safety Daily",
      "feed_description": null,
      "items": []
    }
  }
}
```

- [ ] **Step 3: Write news_empty_feed.json**

```json
{
  "case_name": "news_empty_feed",
  "input": {
    "feed_urls": ["https://example.com/empty"],
    "topics": ["anything"],
    "prior_run_end": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [false]}
    }
  },
  "tool_mocks": {
    "rss_fetch:{\"url\":\"https://example.com/empty\",\"since\":null,\"max_items\":50}": {
      "ok": true,
      "feed_title": "Empty Feed",
      "feed_description": null,
      "items": []
    }
  }
}
```

- [ ] **Step 4: Write news_feed_unreachable.json**

```json
{
  "case_name": "news_feed_unreachable",
  "input": {
    "feed_urls": ["https://unreachable.example.com/feed"],
    "topics": ["AI safety"],
    "prior_run_end": null
  },
  "expected_output_shape": null,
  "tool_mocks": {
    "rss_fetch:{\"url\":\"https://unreachable.example.com/feed\",\"since\":null,\"max_items\":50}": {
      "__error__": "RssFetchError",
      "__message__": "http: 502 Bad Gateway"
    }
  }
}
```

(The `__error__` / `__message__` fixture shape mirrors how Wave 2's `url_404.json` encoded a failure case — see `skills/product_watch/fixtures/url_404.json` and the mock-synthesis helper in `src/donna/skills/mock_synthesis.py` for the runtime contract. If Wave 2's convention differs, adjust to match — the goal is "tool raises ToolError on invocation.")

- [ ] **Step 5: Commit**

```bash
git add skills/news_check/fixtures/
git commit -m "test(skills): seed 4 news_check fixtures with tool_mocks"
```

---

## Task 13: Write email_triage skill artifacts (W4-D8 part 1)

**Files:**
- Create: `skills/email_triage/skill.yaml`
- Create: `skills/email_triage/steps/classify_snippets.md`
- Create: `skills/email_triage/steps/classify_bodies.md`
- Create: `skills/email_triage/steps/render_digest.md`
- Create: `skills/email_triage/schemas/classify_snippets_v1.json`
- Create: `skills/email_triage/schemas/classify_bodies_v1.json`
- Create: `skills/email_triage/schemas/render_digest_v1.json`
- Create: `capabilities/email_triage/input_schema.json`

- [ ] **Step 1: Write skills/email_triage/skill.yaml**

```yaml
capability_name: email_triage
version: 1
description: |
  Scan Gmail for emails from a sender allow-list since the last successful
  run, classify which need a reply, optionally fetch bodies for candidates,
  and DM a digest. Since-last-run semantics via prior_run_end.

inputs:
  schema_ref: capabilities/email_triage/input_schema.json

steps:
  - name: search_messages
    kind: tool
    tools: [gmail_search]
    tool_invocations:
      - tool: gmail_search
        args:
          query: "{{ gmail_query(inputs.senders, inputs.prior_run_end, inputs.query_extras) }}"
          max_results: 20
        retry:
          max_attempts: 2
          backoff_s: [2, 5]
        store_as: search

  - name: classify_snippets
    kind: llm
    prompt: steps/classify_snippets.md
    output_schema: schemas/classify_snippets_v1.json

  - name: classify_bodies
    kind: llm
    prompt: steps/classify_bodies.md
    output_schema: schemas/classify_bodies_v1.json
    skip_if: "{{ state.classify_snippets.candidates | length == 0 }}"

  - name: render_digest
    kind: llm
    prompt: steps/render_digest.md
    output_schema: schemas/render_digest_v1.json

final_output: "{{ state.render_digest }}"
```

Note: the `{{ gmail_query(...) }}` expression composes a Gmail query from `senders`, `prior_run_end`, and optional extras. If the skill DSL doesn't have a built-in helper for this, the composition must happen inside the LLM step's prompt (move the query composition into the `classify_snippets` step by adding a prep step, or implement the helper in the renderer). Confirm at implementation time by running `uv run pytest tests/unit/test_skills_render_helper.py -v`; if function-style helpers aren't supported, add a lightweight `compose_gmail_query` Jinja filter in `src/donna/skills/render.py` as a one-line string builder, or move the composition to a dedicated first LLM step.

- [ ] **Step 2: Write skills/email_triage/steps/classify_snippets.md**

```markdown
You are classifying Gmail messages by action-required likelihood from snippets only.

**Inputs available:**
- `state.search.messages`: list of `{id, sender, subject, snippet, internal_date}`.
- `inputs.senders`: sender allow-list.

**Your job:**
For each message, decide if the snippet alone signals that the user should reply. Signals: direct question, ask, deadline, "please", imperative verb in subject. Automated receipts / notifications / marketing → not action-required.

Return JSON matching the schema. `candidates` is the subset needing body inspection. Keep it short: list only the ambiguous / likely-action-required ones.

Schema:
```
{
  "candidates": [
    {"id": str, "sender": str, "subject": str, "snippet": str, "internal_date": str, "snippet_confidence": float}
  ],
  "total_scanned": int
}
```

`snippet_confidence` ∈ [0, 1]. ≥ 0.6 = include in candidates.
```

- [ ] **Step 3: Write skills/email_triage/schemas/classify_snippets_v1.json**

```json
{
  "type": "object",
  "required": ["candidates", "total_scanned"],
  "properties": {
    "candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "sender", "subject", "snippet", "snippet_confidence"],
        "properties": {
          "id": {"type": "string"},
          "sender": {"type": "string"},
          "subject": {"type": "string"},
          "snippet": {"type": "string"},
          "internal_date": {"type": ["string", "null"]},
          "snippet_confidence": {"type": "number", "minimum": 0, "maximum": 1}
        }
      }
    },
    "total_scanned": {"type": "integer", "minimum": 0}
  }
}
```

- [ ] **Step 4: Write skills/email_triage/steps/classify_bodies.md**

```markdown
You are re-classifying candidate emails using the full body.

**Inputs available:**
- `state.classify_snippets.candidates`: list of candidate messages.
- For each candidate, `state.body.<id>`: the full body from gmail_get_message.
  (If this state isn't populated — e.g., tool not yet invoked — operate on
  the snippet alone and set `body_fetched=false` in the result.)

**Your job:**
Confirm or reject each candidate based on the body. Produce a concise 1-line reason per confirmed item.

Return JSON matching the schema.

Schema:
```
{
  "confirmed": [
    {"id": str, "sender": str, "subject": str, "reason": str, "age_human": str}
  ],
  "rejected_ids": [str],
  "body_fetched": bool
}
```

`age_human`: e.g. "2h ago", "yesterday".
```

Note: The step's tool invocations for `gmail_get_message` aren't present in the skill.yaml — that's because body fetches are dynamic per-candidate. Implement this step's loop by updating the skill executor's LLM context to include a pre-step tool invocation loop, OR add `tool_invocations` blocks to `classify_bodies` that iterate `{{ state.classify_snippets.candidates }}`. If the DSL supports `for_each` tool invocations: use that. Otherwise, simplify Wave 4 by combining classify_snippets and classify_bodies into a single step that operates on snippets only for v1 and add F-W4-H followup for body-fetch in a later wave.

**Decision point:** if `for_each` tool invocation isn't supported by the current executor, go with snippets-only for v1:
- Delete `skills/email_triage/steps/classify_bodies.md`.
- Delete `skills/email_triage/schemas/classify_bodies_v1.json`.
- Delete the `classify_bodies` step from `skill.yaml`.
- Update `render_digest.md` to consume `state.classify_snippets.candidates` directly.
- Drop W4-R15 from the spec and note as F-W4-H followup.

Check: `uv run grep -n "for_each\|tool_invocations_each" src/donna/skills/executor.py`. If absent → simplify.

- [ ] **Step 5: Write skills/email_triage/schemas/classify_bodies_v1.json**

```json
{
  "type": "object",
  "required": ["confirmed", "rejected_ids", "body_fetched"],
  "properties": {
    "confirmed": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "sender", "subject", "reason", "age_human"],
        "properties": {
          "id": {"type": "string"},
          "sender": {"type": "string"},
          "subject": {"type": "string"},
          "reason": {"type": "string"},
          "age_human": {"type": "string"}
        }
      }
    },
    "rejected_ids": {"type": "array", "items": {"type": "string"}},
    "body_fetched": {"type": "boolean"}
  }
}
```

- [ ] **Step 6: Write skills/email_triage/steps/render_digest.md**

```markdown
You are rendering a digest DM summarizing action-required emails.

**Inputs available:**
- `state.classify_bodies.confirmed` (if step ran) OR `state.classify_snippets.candidates` (if skipped).
- `state.classify_snippets.total_scanned`.

**Your job:**
Return JSON matching the schema. Keep `message` under 1200 chars. If zero confirmed, `triggers_alert=false` and `message=null`.

Format each line: `• "<subject>" from <sender> (<age>) — <reason>`.

Schema:
```
{
  "ok": true,
  "triggers_alert": bool,
  "message": string|null,
  "meta": {
    "item_count": int,
    "action_required_count": int,
    "snippet_scanned_count": int,
    "body_fetched_count": int
  }
}
```
```

- [ ] **Step 7: Write skills/email_triage/schemas/render_digest_v1.json**

```json
{
  "type": "object",
  "required": ["ok", "triggers_alert", "message", "meta"],
  "properties": {
    "ok": {"type": "boolean"},
    "triggers_alert": {"type": "boolean"},
    "message": {"type": ["string", "null"]},
    "meta": {
      "type": "object",
      "required": ["item_count", "action_required_count", "snippet_scanned_count", "body_fetched_count"],
      "properties": {
        "item_count": {"type": "integer", "minimum": 0},
        "action_required_count": {"type": "integer", "minimum": 0},
        "snippet_scanned_count": {"type": "integer", "minimum": 0},
        "body_fetched_count": {"type": "integer", "minimum": 0}
      }
    }
  }
}
```

- [ ] **Step 8: Write capabilities/email_triage/input_schema.json**

```json
{
  "type": "object",
  "required": ["senders"],
  "properties": {
    "senders": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 1,
      "description": "Allow-list of sender addresses/domains to scan."
    },
    "query_extras": {
      "type": ["string", "null"],
      "description": "Optional additional Gmail query fragments (e.g. 'is:important')."
    },
    "prior_run_end": {
      "type": ["string", "null"],
      "description": "Injected by dispatcher; skill encodes into 'after:' filter."
    }
  }
}
```

- [ ] **Step 9: Commit**

```bash
git add skills/email_triage/ capabilities/email_triage/
git commit -m "feat(skills): add email_triage skill artifacts (yaml, prompts, schemas)"
```

---

## Task 14: Write email_triage fixtures (W4-D9)

**Files:**
- Create: `skills/email_triage/fixtures/email_two_action_required.json`
- Create: `skills/email_triage/fixtures/email_none_action_required.json`
- Create: `skills/email_triage/fixtures/email_zero_matches.json`
- Create: `skills/email_triage/fixtures/email_gmail_error.json`

- [ ] **Step 1: Write email_two_action_required.json**

```json
{
  "case_name": "email_two_action_required",
  "input": {
    "senders": ["jane@x.com", "team@x.com"],
    "prior_run_end": "2026-04-19T12:00:00+00:00"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert", "message", "meta"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [true]},
      "message": {"type": "string"},
      "meta": {
        "type": "object",
        "properties": {
          "action_required_count": {"type": "integer", "minimum": 2}
        }
      }
    }
  },
  "tool_mocks": {
    "gmail_search:{\"query\":\"from:(jane@x.com OR team@x.com) after:2026/04/19\",\"max_results\":20}": {
      "ok": true,
      "messages": [
        {"id": "m1", "sender": "Jane <jane@x.com>", "subject": "Re: Q2 roadmap",
         "snippet": "Can you confirm the timelines by Friday?",
         "internal_date": "2026-04-20T08:00:00+00:00"},
        {"id": "m2", "sender": "team@x.com", "subject": "Budget approval needed",
         "snippet": "Please approve the attached budget request.",
         "internal_date": "2026-04-20T06:00:00+00:00"},
        {"id": "m3", "sender": "Jane <jane@x.com>", "subject": "Read: conference video",
         "snippet": "Here is the conference recording.",
         "internal_date": "2026-04-20T04:00:00+00:00"}
      ]
    }
  }
}
```

- [ ] **Step 2: Write email_none_action_required.json**

```json
{
  "case_name": "email_none_action_required",
  "input": {
    "senders": ["notifications@x.com"],
    "prior_run_end": "2026-04-20T00:00:00+00:00"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [false]}
    }
  },
  "tool_mocks": {
    "gmail_search:{\"query\":\"from:(notifications@x.com) after:2026/04/20\",\"max_results\":20}": {
      "ok": true,
      "messages": [
        {"id": "n1", "sender": "notifications@x.com", "subject": "Your weekly summary",
         "snippet": "Here is your activity recap.",
         "internal_date": "2026-04-20T01:00:00+00:00"}
      ]
    }
  }
}
```

- [ ] **Step 3: Write email_zero_matches.json**

```json
{
  "case_name": "email_zero_matches",
  "input": {
    "senders": ["nobody@x.com"],
    "prior_run_end": "2026-04-20T00:00:00+00:00"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert"],
    "properties": {
      "ok": {"enum": [true]},
      "triggers_alert": {"enum": [false]}
    }
  },
  "tool_mocks": {
    "gmail_search:{\"query\":\"from:(nobody@x.com) after:2026/04/20\",\"max_results\":20}": {
      "ok": true,
      "messages": []
    }
  }
}
```

- [ ] **Step 4: Write email_gmail_error.json**

```json
{
  "case_name": "email_gmail_error",
  "input": {
    "senders": ["jane@x.com"],
    "prior_run_end": null
  },
  "expected_output_shape": null,
  "tool_mocks": {
    "gmail_search:{\"query\":\"from:(jane@x.com)\",\"max_results\":20}": {
      "__error__": "GmailToolError",
      "__message__": "search: token expired"
    }
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add skills/email_triage/fixtures/
git commit -m "test(skills): seed 4 email_triage fixtures with tool_mocks"
```

---

## Task 15: Add news_check + email_triage entries to config/capabilities.yaml (W4-D11)

**Files:**
- Modify: `config/capabilities.yaml`

- [ ] **Step 1: Append entries to config/capabilities.yaml**

Add after the existing `product_watch` block:

```yaml
  - name: news_check
    description: |
      Monitor RSS/Atom feeds for new items matching user-specified topics.
      Uses prior_run_end to filter items since the last successful run.
      Alerts when new items match any topic in the inputs.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [feed_urls, topics]
      properties:
        feed_urls:
          type: array
          items: {type: string}
          minItems: 1
          description: List of RSS/Atom feed URLs to monitor.
        topics:
          type: array
          items: {type: string}
          minItems: 1
          description: Topic keywords to match feed items against.
        prior_run_end:
          type: ["string", "null"]
          description: Injected by dispatcher; skill passes to rss_fetch.since.
    default_output_shape:
      type: object
      required: [ok, triggers_alert, message, meta]
      properties:
        ok: {type: boolean}
        triggers_alert: {type: boolean}
        message: {type: ["string", "null"]}
        meta:
          type: object

  - name: email_triage
    description: |
      Scan Gmail for emails from a sender allow-list since the last run,
      classify which need a reply, DM a digest when any do. Depends on the
      gmail_search (+ optional gmail_get_message) tool being registered.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [senders]
      properties:
        senders:
          type: array
          items: {type: string}
          minItems: 1
          description: Allow-list of sender addresses/domains to scan.
        query_extras:
          type: ["string", "null"]
          description: Optional additional Gmail query fragments.
        prior_run_end:
          type: ["string", "null"]
          description: Injected by dispatcher; encoded into 'after:' filter.
    default_output_shape:
      type: object
      required: [ok, triggers_alert, message, meta]
      properties:
        ok: {type: boolean}
        triggers_alert: {type: boolean}
        message: {type: ["string", "null"]}
        meta:
          type: object
```

- [ ] **Step 2: Verify YAML parses**

Run: `uv run python -c "import yaml; d = yaml.safe_load(open('config/capabilities.yaml')); assert len(d['capabilities']) == 3; print([c['name'] for c in d['capabilities']])"`
Expected: prints `['product_watch', 'news_check', 'email_triage']`.

- [ ] **Step 3: Run SeedCapabilityLoader tests**

Run: `uv run pytest tests/unit/ -k seed_capability -v`
Expected: All pass (loader is data-driven).

- [ ] **Step 4: Commit**

```bash
git add config/capabilities.yaml
git commit -m "feat(config): register news_check + email_triage in capabilities.yaml"
```

---

## Task 16: Write Alembic seed migration (W4-D6 + W4-D8 persistence)

**Files:**
- Create: `alembic/versions/f3a4b5c6d7e8_seed_news_check_and_email_triage.py`

- [ ] **Step 1: Find the current head revision**

Run: `uv run alembic heads`
Expected: prints one revision id (the current head). Call it `<HEAD_REV>`.

- [ ] **Step 2: Write the migration file**

Substitute `<HEAD_REV>` with the revision printed in step 1. Model the migration closely on `alembic/versions/seed_product_watch_capability.py`:

```python
"""seed news_check + email_triage capabilities + skills + fixtures

Revision ID: f3a4b5c6d7e8
Revises: <HEAD_REV>
Create Date: 2026-04-20 00:00:00.000000
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "<HEAD_REV>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CAP_NAMES = ("news_check", "email_triage")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _seed_one(conn, capability_name: str, caps_config: list[dict], now: str) -> None:
    root = _project_root()
    skill_dir = root / "skills" / capability_name

    cap_entry = next((c for c in caps_config if c.get("name") == capability_name), None)
    if cap_entry is None:
        raise RuntimeError(f"{capability_name} missing from config/capabilities.yaml")

    capability_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, default_output_shape, status, created_at, created_by) "
        "VALUES (:id, :name, :desc, :schema, :trigger, :shape, 'active', :now, 'seed')"
    ), {
        "id": capability_id,
        "name": capability_name,
        "desc": cap_entry.get("description", ""),
        "schema": json.dumps(cap_entry.get("input_schema", {})),
        "trigger": cap_entry.get("trigger_type", "on_schedule"),
        "shape": json.dumps(cap_entry.get("default_output_shape", {})),
        "now": now,
    })

    # Skill + version (sandbox).
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, created_at, updated_at) "
        "VALUES (:id, :cap, :vid, 'sandbox', 0, :now, :now)"
    ), {"id": skill_id, "cap": capability_name, "vid": version_id, "now": now})

    yaml_backbone = _read(skill_dir / "skill.yaml")

    step_content: dict[str, str] = {}
    output_schemas: dict[str, dict] = {}
    for step_md in sorted((skill_dir / "steps").glob("*.md")):
        step_name = step_md.stem
        step_content[step_name] = _read(step_md)
    for schema_json in sorted((skill_dir / "schemas").glob("*.json")):
        # schema filenames are "<step>_v1.json"
        step_name = schema_json.stem.rsplit("_v", 1)[0]
        output_schemas[step_name] = json.loads(_read(schema_json))

    conn.execute(sa.text(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) "
        "VALUES (:id, :sid, 1, :yaml, :steps, :schemas, 'seed', 'initial v1', :now)"
    ), {
        "id": version_id, "sid": skill_id,
        "yaml": yaml_backbone,
        "steps": json.dumps(step_content),
        "schemas": json.dumps(output_schemas),
        "now": now,
    })

    # Fixtures.
    fixtures_dir = skill_dir / "fixtures"
    for fixture_file in sorted(fixtures_dir.glob("*.json")):
        fixture = json.loads(_read(fixture_file))
        conn.execute(sa.text(
            "INSERT INTO skill_fixture "
            "(id, skill_id, case_name, input, expected_output_shape, "
            " source, captured_run_id, created_at, tool_mocks) "
            "VALUES (:id, :sid, :case, :input, :shape, 'human_written', "
            "         NULL, :now, :mocks)"
        ), {
            "id": str(uuid.uuid4()),
            "sid": skill_id,
            "case": fixture["case_name"],
            "input": json.dumps(fixture["input"]),
            "shape": (
                json.dumps(fixture["expected_output_shape"])
                if fixture.get("expected_output_shape") else None
            ),
            "now": now,
            "mocks": (
                json.dumps(fixture["tool_mocks"])
                if fixture.get("tool_mocks") else None
            ),
        })


def upgrade() -> None:
    root = _project_root()
    conn = op.get_bind()
    now = datetime.now(tz=timezone.utc).isoformat()

    capabilities_yaml = root / "config" / "capabilities.yaml"
    caps = yaml.safe_load(_read(capabilities_yaml)).get("capabilities", [])

    for name in CAP_NAMES:
        _seed_one(conn, name, caps, now)


def downgrade() -> None:
    conn = op.get_bind()
    for name in CAP_NAMES:
        conn.execute(sa.text(
            "DELETE FROM skill_fixture WHERE skill_id IN "
            "(SELECT id FROM skill WHERE capability_name = :cap)"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM skill_version WHERE skill_id IN "
            "(SELECT id FROM skill WHERE capability_name = :cap)"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM skill WHERE capability_name = :cap"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM capability WHERE name = :cap"
        ), {"cap": name})
```

- [ ] **Step 3: Verify migration applies and rolls back cleanly**

```bash
uv run alembic upgrade head
uv run python -c "
import sqlite3
conn = sqlite3.connect('donna_tasks.db')  # use whatever path alembic targets
rows = conn.execute(\"SELECT name FROM capability WHERE name IN ('news_check','email_triage')\").fetchall()
print(rows)
assert len(rows) == 2
"
uv run alembic downgrade -1
uv run python -c "
import sqlite3
conn = sqlite3.connect('donna_tasks.db')
rows = conn.execute(\"SELECT name FROM capability WHERE name IN ('news_check','email_triage')\").fetchall()
print(rows)
assert len(rows) == 0
"
uv run alembic upgrade head
```
Expected: both capabilities land, downgrade removes them, re-upgrade restores.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/f3a4b5c6d7e8_seed_news_check_and_email_triage.py
git commit -m "feat(alembic): seed news_check + email_triage capabilities, skills, fixtures"
```

---

## Task 17: Write failing test for capability-availability guard (W4-D10 part 1)

**Files:**
- Create: `tests/unit/test_creation_path_capability_guard.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests: AutomationCreationPath rejects approval when required tool is missing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.creation_flow import (
    AutomationCreationPath,
    MissingToolError,
)
from donna.skills.tool_registry import ToolRegistry
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


def _make_draft(**overrides):
    base = dict(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["jane@x.com"]},
        schedule_cron="0 */12 * * *",
        target_cadence_cron="0 */12 * * *",
        active_cadence_cron="0 */12 * * *",
        alert_conditions={},
    )
    base.update(overrides)
    return DraftAutomation(**base)


@pytest.mark.asyncio
async def test_approve_rejects_when_required_tool_unregistered():
    reg = ToolRegistry()
    # Only register web_fetch + rss_fetch — gmail_search absent.
    reg.register("web_fetch", AsyncMock())
    reg.register("rss_fetch", AsyncMock())

    required_lookup = AsyncMock(return_value=["gmail_search"])
    repo = AsyncMock()
    path = AutomationCreationPath(
        repository=repo,
        tool_registry=reg,
        capability_tool_lookup=required_lookup,
    )

    with pytest.raises(MissingToolError) as ei:
        await path.approve(_make_draft(), name="triage-jane")
    assert "gmail_search" in str(ei.value)
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_approve_proceeds_when_tools_registered():
    reg = ToolRegistry()
    reg.register("gmail_search", AsyncMock())

    required_lookup = AsyncMock(return_value=["gmail_search"])
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="auto1")

    path = AutomationCreationPath(
        repository=repo,
        tool_registry=reg,
        capability_tool_lookup=required_lookup,
    )

    out = await path.approve(_make_draft(), name="triage-jane")
    assert out == "auto1"
    repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_approve_backward_compat_without_guard_deps():
    """When tool_registry/lookup aren't wired, approve() behaves as before."""
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="auto2")
    path = AutomationCreationPath(repository=repo)
    out = await path.approve(_make_draft(), name="triage-jane")
    assert out == "auto2"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_creation_path_capability_guard.py -v`
Expected: `ImportError: cannot import name 'MissingToolError' from ...` on the first import; other tests don't reach evaluation yet.

---

## Task 18: Implement capability-availability guard (W4-D10 part 2)

**Files:**
- Modify: `src/donna/automations/creation_flow.py`

- [ ] **Step 1: Update `AutomationCreationPath` to accept the guard dependencies**

Replace the content of `src/donna/automations/creation_flow.py`:

```python
"""AutomationCreationPath — final step of the Discord NL creation flow.

Invoked when the user clicks Approve on an AutomationConfirmationView.
Writes the automation row. Idempotent on (user_id, name) uniqueness — a
second approve returns ``None`` instead of creating a duplicate.

Wave 4: capability-availability guard. Before writing, verify all tools
the capability's skill depends on are registered. If not, raise
MissingToolError so the caller can DM an actionable error.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from donna.automations.repository import AlreadyExistsError
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

logger = structlog.get_logger()


class MissingToolError(Exception):
    """Raised when a capability needs a tool that isn't currently registered."""

    def __init__(self, capability: str, missing: list[str]) -> None:
        super().__init__(
            f"capability {capability!r} requires unregistered tool(s): {missing}"
        )
        self.capability = capability
        self.missing = missing


CapabilityToolLookup = Callable[[str], Awaitable[list[str]]]


class AutomationCreationPath:
    def __init__(
        self,
        *,
        repository: Any,
        default_min_interval_seconds: int = 300,
        tool_registry: Any | None = None,
        capability_tool_lookup: CapabilityToolLookup | None = None,
    ) -> None:
        self._repo = repository
        self._default_min_interval_seconds = default_min_interval_seconds
        self._tool_registry = tool_registry
        self._capability_tool_lookup = capability_tool_lookup

    async def approve(self, draft: DraftAutomation, *, name: str) -> str | None:
        capability_name = draft.capability_name or "claude_native"

        # Capability-availability guard: only when wired (preserves
        # backward-compat for tests that construct without registry).
        if (
            self._tool_registry is not None
            and self._capability_tool_lookup is not None
            and draft.capability_name  # placeholder has no tool requirements
        ):
            required = await self._capability_tool_lookup(draft.capability_name)
            available = set(self._tool_registry.list_tool_names())
            missing = [t for t in required if t not in available]
            if missing:
                logger.warning(
                    "automation_creation_missing_tools",
                    capability=draft.capability_name,
                    missing=missing,
                )
                raise MissingToolError(draft.capability_name, missing)

        try:
            automation_id = await self._repo.create(
                user_id=draft.user_id,
                name=name,
                description=None,
                capability_name=capability_name,
                inputs=draft.inputs,
                trigger_type="on_schedule",
                schedule=draft.schedule_cron,
                alert_conditions=draft.alert_conditions or {},
                alert_channels=["discord_dm"],
                max_cost_per_run_usd=None,
                min_interval_seconds=self._default_min_interval_seconds,
                created_via="discord",
                target_cadence_cron=draft.target_cadence_cron,
                active_cadence_cron=draft.active_cadence_cron,
            )
            logger.info(
                "automation_created_via_discord",
                user_id=draft.user_id,
                name=name,
                capability=draft.capability_name,
                target_cadence=draft.target_cadence_cron,
                active_cadence=draft.active_cadence_cron,
            )
            return automation_id
        except AlreadyExistsError:
            logger.info(
                "automation_creation_already_exists",
                user_id=draft.user_id,
                name=name,
            )
            return None
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/unit/test_creation_path_capability_guard.py -v`
Expected: All three pass.

- [ ] **Step 3: Run the existing creation-flow regression suite**

Run: `uv run pytest tests/unit/ -k creation_flow -v`
Expected: All pass (existing tests pass the old 1-arg constructor; guard is opt-in).

- [ ] **Step 4: Wire the new dependencies in cli_wiring.py**

Find where `AutomationCreationPath` is constructed today. Extend the call:

```python
creation_path = AutomationCreationPath(
    repository=automation_repo,
    default_min_interval_seconds=cfg.default_min_interval_seconds,
    tool_registry=DEFAULT_TOOL_REGISTRY,
    capability_tool_lookup=capability_repo.list_required_tools,
)
```

If `capability_repo` doesn't have a `list_required_tools` method yet, add one. It should parse the skill YAML's `steps[*].tools` allowlist for the latest `skill_version` of the capability and return the union (a ~20-line helper on the repo — or a free function `compute_required_tools(version: SkillVersion) -> list[str]` used by the lookup).

- [ ] **Step 5: Wire the guard's error into the Discord approve-button handler**

Find `AutomationConfirmationView`'s approve callback (in `src/donna/integrations/discord_views.py`). Wrap the `await creation_path.approve(...)` call in `try: ... except MissingToolError as exc:` and DM:

```
"I can't run `{exc.capability}` until {', '.join(exc.missing)} is connected — set that up first and try again."
```

- [ ] **Step 6: Run the existing Discord view tests**

Run: `uv run pytest tests/unit/ -k discord_view -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/creation_flow.py src/donna/cli_wiring.py src/donna/integrations/discord_views.py tests/unit/test_creation_path_capability_guard.py
git commit -m "feat(automations): capability-availability guard at approval time"
```

---

## Task 19: Write news_check E2E test (W4-D12)

**Files:**
- Create: `tests/e2e/test_wave4_news_check.py`

Use `tests/e2e/test_wave2_product_watch.py` as a structural template — same fixtures (`runtime`), same mocking idioms, same promotion helper.

- [ ] **Step 1: Write the E2E file**

```python
"""Wave 4 E2E — news_check: NL creation, since-last-run filter, shadow promotion."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


RSS_RESPONSE_NEW = {
    "ok": True,
    "feed_title": "AI Safety Daily",
    "feed_description": "",
    "items": [
        {
            "title": "Alignment interpretability paper",
            "link": "https://example.com/a1",
            "published": "2026-04-20T08:00:00+00:00",
            "author": "alice",
            "summary": "Scalable interpretability.",
        },
        {
            "title": "Policy brief: AI safety",
            "link": "https://example.com/a2",
            "published": "2026-04-20T06:00:00+00:00",
            "author": "bob",
            "summary": "Regulatory overview.",
        },
    ],
}

RSS_RESPONSE_NONE = {"ok": True, "feed_title": "AI Safety Daily", "items": []}


@pytest.mark.asyncio
async def test_news_check_nl_creation_then_first_tick_alerts(runtime):
    """AS-W4.1 — NL creation → first tick → 2 new items → DM."""
    runtime.patch_tool("rss_fetch", lambda **kwargs: RSS_RESPONSE_NEW)

    dm = await runtime.simulate_discord_dm(
        user_id="u1",
        text="watch https://example.com/feed for articles about AI safety every 12 hours",
    )
    assert dm.confirmation_posted is True

    auto_id = await runtime.approve_confirmation(dm)

    runtime.advance_to_due(auto_id)
    report = await runtime.run_scheduler_tick()
    assert report.alert_sent is True
    assert "Alignment interpretability" in report.alert_content or "Policy brief" in report.alert_content


@pytest.mark.asyncio
async def test_news_check_second_tick_filters_by_prior_run_end(runtime):
    """AS-W4.2 — second tick with prior_run_end populated returns 0 items, no DM."""
    runtime.patch_tool("rss_fetch", lambda **kwargs: RSS_RESPONSE_NEW)

    auto_id = await runtime.seed_automation(
        capability="news_check",
        user_id="u1",
        inputs={"feed_urls": ["https://example.com/feed"], "topics": ["AI safety"]},
        schedule="0 */12 * * *",
    )
    # First tick creates a completed automation_run.
    runtime.advance_to_due(auto_id); await runtime.run_scheduler_tick()

    # Now flip the tool to return empty. The dispatcher should inject
    # prior_run_end = first run's end_time; rss_fetch mock should be called
    # with since=<that>.
    captured_args = []
    runtime.patch_tool("rss_fetch", lambda **kwargs: (captured_args.append(kwargs), RSS_RESPONSE_NONE)[1])

    runtime.advance_to_due(auto_id); report2 = await runtime.run_scheduler_tick()
    assert report2.alert_sent is False
    assert captured_args and captured_args[0].get("since") is not None


@pytest.mark.asyncio
async def test_news_check_promotion_to_shadow_primary_fires_skill_executor(runtime):
    """AS-W4.3 — 20 successful shadow runs → shadow_primary → SkillExecutor path."""
    runtime.seed_shadow_runs(capability="news_check", count=20, agreement=0.95)
    runtime.promote_skill(capability="news_check", to_state="shadow_primary")

    runtime.patch_tool("rss_fetch", lambda **kwargs: RSS_RESPONSE_NEW)
    auto_id = await runtime.seed_automation(
        capability="news_check", user_id="u1",
        inputs={"feed_urls": ["https://example.com/feed"], "topics": ["AI safety"]},
        schedule="0 */12 * * *",
    )
    runtime.advance_to_due(auto_id)
    report = await runtime.run_scheduler_tick()
    assert report.execution_path == "skill"
    assert report.skill_run_id is not None
```

The `runtime` fixture may need helpers it doesn't have today — `patch_tool`, `seed_automation`, `seed_shadow_runs`, `promote_skill`, `simulate_discord_dm`, `approve_confirmation`, `advance_to_due`, `run_scheduler_tick`. If Wave 2/3 already defines these, reuse verbatim. If not, extend `tests/e2e/harness.py` to add thin wrappers over the underlying primitives. Check with `uv run grep -n "def patch_tool\|def seed_automation\|def seed_shadow_runs" tests/e2e/harness.py` before writing new code.

- [ ] **Step 2: Run the E2E**

Run: `uv run pytest tests/e2e/test_wave4_news_check.py -v`
Expected: All three pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave4_news_check.py tests/e2e/harness.py
git commit -m "test(e2e): Wave 4 AS-W4.1/.2/.3 — news_check NL + since-filter + shadow"
```

---

## Task 20: Write email_triage E2E test (W4-D13)

**Files:**
- Create: `tests/e2e/test_wave4_email_triage.py`

- [ ] **Step 1: Write the E2E file**

```python
"""Wave 4 E2E — email_triage: NL creation, two-stage classify, guard."""
from __future__ import annotations

import pytest


MSG1 = {
    "id": "m1", "sender": "Jane <jane@x.com>", "subject": "Re: Q2 roadmap",
    "snippet": "Can you confirm timelines by Friday?",
    "internal_date": "2026-04-20T08:00:00+00:00",
}
MSG2 = {
    "id": "m2", "sender": "team@x.com", "subject": "Budget approval needed",
    "snippet": "Please approve the attached budget request.",
    "internal_date": "2026-04-20T06:00:00+00:00",
}
MSG3 = {
    "id": "m3", "sender": "Jane <jane@x.com>", "subject": "FYI conference recording",
    "snippet": "Here is the conference recording.",
    "internal_date": "2026-04-20T04:00:00+00:00",
}


@pytest.mark.asyncio
async def test_email_triage_nl_creation_action_required_digest(runtime):
    """AS-W4.4 — 3 search results → 2 classified action-required → digest DM."""
    search_return = {"ok": True, "messages": [MSG1, MSG2, MSG3]}
    get_message_calls: list[str] = []

    def _get_message(**kwargs):
        get_message_calls.append(kwargs["message_id"])
        return {
            "ok": True,
            "sender": kwargs["message_id"],
            "subject": "body",
            "body_plain": "please reply",
            "body_html": None,
            "internal_date": "2026-04-20T08:00:00+00:00",
            "headers": {},
        }

    runtime.patch_tool("gmail_search", lambda **kw: search_return)
    runtime.patch_tool("gmail_get_message", _get_message)

    dm = await runtime.simulate_discord_dm(
        user_id="u1",
        text="tell me about action-required emails from jane@x.com or team@x.com every 12 hours",
    )
    auto_id = await runtime.approve_confirmation(dm)

    runtime.advance_to_due(auto_id)
    report = await runtime.run_scheduler_tick()
    assert report.alert_sent is True

    # Step 3 should only fetch the 2 candidates, not all 3 messages.
    assert len(get_message_calls) == 2
    assert "m3" not in get_message_calls


@pytest.mark.asyncio
async def test_email_triage_guard_rejects_when_gmail_missing(runtime):
    """AS-W4.6 — GmailClient absent → guard rejects approval with DM."""
    runtime.clear_tool("gmail_search")
    runtime.clear_tool("gmail_get_message")

    dm = await runtime.simulate_discord_dm(
        user_id="u1",
        text="tell me about action-required emails from jane@x.com every 12 hours",
    )
    reply = await runtime.approve_confirmation_expect_error(dm)
    assert "gmail" in reply.lower() or "connect" in reply.lower()

    # No automation row created.
    assert await runtime.count_automations(user_id="u1", capability="email_triage") == 0
```

If `runtime` doesn't yet expose `clear_tool` / `approve_confirmation_expect_error` / `count_automations`, add them to `tests/e2e/harness.py` as thin wrappers (a few lines each).

- [ ] **Step 2: Run the E2E**

Run: `uv run pytest tests/e2e/test_wave4_email_triage.py -v`
Expected: Both tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave4_email_triage.py tests/e2e/harness.py
git commit -m "test(e2e): Wave 4 AS-W4.4/.5/.6 — email_triage + capability guard"
```

---

## Task 21: Write cross-capability integration test (W4-D14)

**Files:**
- Create: `tests/e2e/test_wave4_full_stack.py`

- [ ] **Step 1: Write the test**

```python
"""Wave 4 cross-capability — one tick, three automations, no cross-talk."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_full_stack_single_tick_isolates_runs(runtime):
    """AS-W4.7 — product_watch + news_check + email_triage in one tick.
    Assert per-automation prior_run_end isolation, per-run tool_mocks, and
    alert dispatch for only the alerting automations.
    """
    # Tool mocks: product_watch alerts, news_check alerts, email_triage quiet.
    runtime.patch_tool("web_fetch", lambda **kw: {
        "status_code": 200,
        "body": "<html><body><span class='price'>$79</span>"
                "<div class='sizes'>S, M, L, XL</div>"
                "<div class='stock'>In stock</div></body></html>",
        "headers": {},
    })
    runtime.patch_tool("rss_fetch", lambda **kw: {
        "ok": True, "feed_title": "f", "items": [
            {"title": "AI safety paper", "link": "u", "published": "2026-04-20T08:00:00+00:00",
             "author": "a", "summary": "alignment advance"},
        ],
    })
    runtime.patch_tool("gmail_search", lambda **kw: {"ok": True, "messages": []})

    prod_id = await runtime.seed_automation(
        capability="product_watch", user_id="u1",
        inputs={"url": "https://shop.example.com/jacket",
                "max_price_usd": 100, "required_size": "L"},
        schedule="0 */12 * * *",
    )
    news_id = await runtime.seed_automation(
        capability="news_check", user_id="u1",
        inputs={"feed_urls": ["https://example.com/feed"], "topics": ["AI safety"]},
        schedule="0 */12 * * *",
    )
    mail_id = await runtime.seed_automation(
        capability="email_triage", user_id="u1",
        inputs={"senders": ["nobody@x.com"]},
        schedule="0 */12 * * *",
    )

    runtime.advance_to_due(prod_id)
    runtime.advance_to_due(news_id)
    runtime.advance_to_due(mail_id)

    reports = await runtime.run_scheduler_tick_all_due()
    by_cap = {r.capability_name: r for r in reports}

    assert by_cap["product_watch"].alert_sent is True
    assert by_cap["news_check"].alert_sent is True
    assert by_cap["email_triage"].alert_sent is False

    # Three distinct automation_run rows, one per automation.
    assert len(reports) == 3
    assert len({r.automation_id for r in reports}) == 3

    # No shared tool-mock state: all three produced their own output.
    for r in reports:
        assert r.status == "succeeded"
```

If `run_scheduler_tick_all_due` doesn't exist yet, add it in `harness.py` — iterate `repo.list_due(now)` and dispatch each, collecting reports.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/e2e/test_wave4_full_stack.py -v`
Expected: Passes.

- [ ] **Step 3: Run the full E2E suite once to catch cross-test interactions**

Run: `uv run pytest tests/e2e/ -v`
Expected: All Wave 1/2/3/4 E2Es pass. If Wave 4 tests fail when run alongside Wave 2 due to tool-registry leakage, this surfaces F-W2-B — escalate per spec §8 and implement `ToolRegistry.clear()` plus a `conftest.py` autouse fixture that resets the registry between tests.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_wave4_full_stack.py tests/e2e/harness.py
git commit -m "test(e2e): cross-capability Wave 4 integration — no cross-talk on single tick"
```

---

## Task 22: Update followups inventory (W4-D15)

**Files:**
- Modify: `docs/superpowers/followups/2026-04-16-skill-system-followups.md`

- [ ] **Step 1: Update the Wave 3 followups section**

Open the file. Find the `## Follow-ups surfaced during Wave 3 (2026-04-17)` heading. Transform each F-W3-A through F-W3-K entry to indicate closure, e.g.:

```markdown
- **F-W3-A — CadencePolicy override precedence.** ✅ Closed in commit `9ae2b8d` (2026-04-17).
- **F-W3-B — PendingDraftRegistry sweeper race condition.** ✅ Closed in commit `9ae2b8d`.
- **F-W3-C — Legacy dedup_pending + field-update handlers unreachable.** ✅ Closed in commit `50794a1`.
- **F-W3-D — DonnaBot _TasksDbAdapter stuffs capability_name + inputs into notes JSON.** ✅ Closed in commit `50794a1`.
- **F-W3-E — AutomationConfirmationView approval coroutine timeout.** (P3, unchanged — not addressed.)
- **F-W3-F — AutomationConfirmationView edit branch is log-only.** ✅ Closed in commit `50794a1`.
- **F-W3-G — Challenger parse schema + prompt drift risk.** ✅ Closed in commit `9ae2b8d`.
- **F-W3-H — Challenger parse schema validation not invoked.** ✅ Closed in commit `9ae2b8d`.
- **F-W3-I — DiscordHandle.notification_service duplication.** ✅ Closed in commit `9ae2b8d`.
- **F-W3-J — SkillSystemHandle.skill_router naming misleading.** ✅ Closed in commit `9ae2b8d`.
- **F-W3-K — Challenger parse snapshot_capabilities is un-cached.** ✅ Closed in commit `9ae2b8d`.
```

- [ ] **Step 2: Add Wave 4 completion section after the Wave 3 completed section**

Insert between `## Completed — Wave 3 (2026-04-17)` block and `## Follow-ups surfaced during Wave 3`:

```markdown
## Completed — Wave 4 (2026-04-20)

- **news_check** seed capability — RSS/Atom monitoring with since-last-run semantics. `rss_fetch` tool + skill + 4 fixtures + Alembic seed.
- **email_triage** seed capability — Gmail action-required scan with since-last-run semantics. `gmail_search` + `gmail_get_message` tools + skill + 4 fixtures + Alembic seed.
- **Dispatcher `prior_run_end` injection** — `AutomationDispatcher` queries most recent successful `automation_run.end_time` and injects as skill input. Zero schema changes.
- **`register_default_tools(gmail_client=...)`** — optional GmailClient threading; Gmail tools register only when client is available.
- **Capability-availability guard** — `AutomationCreationPath` rejects approval with actionable DM when a required tool is unregistered.
- **Digest-shape alert contract** — codified as default for multi-hit capabilities via uniform `{ok, triggers_alert, message, meta}` output.
- **Cross-capability integration test** — single-tick dispatch of product_watch + news_check + email_triage with isolation assertions; rolls in F-14 intent.
- **Wave 3 P2/P3 rollup** — doc drift repaired; F-W3-A through K marked closed with commit refs.

See `docs/superpowers/specs/2026-04-20-skill-system-wave-4-news-and-email-capabilities-design.md`.
```

- [ ] **Step 3: Append Wave 4 followups section after the Wave 4 completed section**

```markdown
## Follow-ups surfaced during Wave 4 (2026-04-20)

- **F-W4-A — `email_triage` unbounded-sender mode.** *(P2.)* Scan all inbound mail for action-required, not just a sender allow-list. Different privacy shape + token cost profile. Wait for concrete user ask.
- **F-W4-B — Pagination for `gmail_search` / `rss_fetch`.** *(P3.)* Trigger: observed context-overflow escalations on either capability.
- **F-W4-C — `html_extract` tool for non-RSS news sites.** *(P3.)* Trigger: a concrete user-named non-RSS source.
- **F-W4-D — Per-automation skill-state blob.** *(P3.)* Alternative to since-last-run semantics if a capability needs richer state carryover. Speculative today.
- **F-W4-E — Dashboard surfacing of `meta.*` per-run diagnostics.** *(P2.)* Depends on F-4 dashboard. Wave 5+.
- **F-W4-F — `ToolRegistry.clear()` + pytest fixture.** *(P3 → escalate to P1 if cross-test leakage surfaces during Wave 4 E2E.)* Upgrade of F-W2-B.
- **F-W4-G — First-run digest backlog cap in NotificationService.** *(P3.)* Today enforced in skill render prompt; eventually belongs in notification layer.
- **F-W4-H — `for_each` tool invocations in skill DSL.** *(P2 if email_triage was simplified to snippets-only in Wave 4.)* Needed for proper body-fetch iteration in `email_triage` step 3.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/followups/2026-04-16-skill-system-followups.md
git commit -m "docs(followups): mark Wave 3 P2/P3 items closed; record Wave 4 completion + new followups"
```

---

## Task 23: Final verification sweep

**Files:** (none modified)

- [ ] **Step 1: Run the full unit test suite**

Run: `uv run pytest tests/unit/ -v`
Expected: All pass. If any Wave 2/3 regressions surface, investigate and fix before Wave 4 merges — they're not "known" and must be addressed.

- [ ] **Step 2: Run the full E2E suite**

Run: `uv run pytest tests/e2e/ -v`
Expected: All Wave 1/2/3/4 E2Es pass.

- [ ] **Step 3: Run alembic round-trip once more**

```bash
uv run alembic upgrade head
uv run alembic downgrade -2   # peel off news_check + email_triage seed
uv run alembic upgrade head   # re-apply
```
Expected: clean up/down, idempotent final state.

- [ ] **Step 4: Smoke-boot the orchestrator briefly (if local env permits)**

Run: `timeout 10 uv run python -m donna.cli serve-orchestrator || true`
Expected: No tracebacks during startup wiring. Look for log lines confirming the new capabilities and tools register:
- `capability_loaded ... name=news_check`
- `capability_loaded ... name=email_triage`
- `tool_registered ... name=rss_fetch`
- `tool_registered ... name=gmail_search` (only if GmailClient is available)

- [ ] **Step 5: Run superpowers:verification-before-completion**

Before declaring Wave 4 complete, invoke the `superpowers:verification-before-completion` skill to confirm all acceptance scenarios (AS-W4.1–AS-W4.12) have a passing test and every requirement in §7 of the spec maps to implemented code.

- [ ] **Step 6: Request code review**

Invoke the `superpowers:requesting-code-review` skill to run a review against the spec before merge. This should catch any drift between the approved design and the shipped code.

---

## Self-Review

**Spec coverage** (each §7 requirement → task):

- W4-R1 (rss_fetch tool with since filter) → Task 2-3.
- W4-R2 (gmail_search read-only) → Task 4-5.
- W4-R3 (gmail_get_message plain-text preference) → Task 4-5.
- W4-R4 (register_default_tools conditional Gmail) → Task 6-7.
- W4-R5 (dispatcher prior_run_end) → Task 9-10.
- W4-R6 (news_check seeded + discoverable) → Task 11, 15, 16.
- W4-R7 (email_triage seeded + discoverable) → Task 13, 15, 16.
- W4-R8 (4 fixtures per capability) → Task 12, 14.
- W4-R9 (availability guard) → Task 17-18.
- W4-R10 (sandbox landing + Wave 1 gate) → Task 16 (seed state='sandbox') + Task 19 promotion helper.
- W4-R11 (skill_step task_types in invocation_log) → emerges from skill YAML step names; verified by Task 23 smoke boot.
- W4-R12 (digest shape across all Wave 4 capabilities) → Task 11, 13 schemas.
- W4-R13 (on_failure=escalate default) → no change needed; existing Wave 3 DSL default applies.
- W4-R14 (cross-capability isolation) → Task 21.
- W4-R15 (email_triage step 3 gated by step 2 candidates) → Task 13 skill.yaml `skip_if`.
- W4-R16 (cadence policy applies to new caps) → no-change, existing infra; verified indirectly by Task 19 promotion.
- W4-R17 (followups doc closure + Wave 4 stub) → Task 22.
- W4-R18 (zero schema changes) → verified structurally; migration only seeds rows.

All 18 requirements have a task.

**Placeholder scan**: no "TBD", "TODO", or "handle edge cases" fallthroughs. Every step has code or an exact command.

**Type consistency**: `MissingToolError` introduced in Task 17, used in Tasks 17-18. `RssFetchError` Task 3. `GmailToolError` Task 5 (re-imported in gmail_get_message.py). `DraftAutomation` referenced in Task 17-18 uses existing Wave 3 dataclass — no shape drift.

**Risk not covered**: Task 13 contains a DSL-capability decision point (`for_each` tool invocations) that may require simplification to snippets-only if the executor doesn't support iteration. Captured as F-W4-H in the followups update (Task 22). Not a blocker, but an engineer should flag this in the code review for Wave 5 scoping.
