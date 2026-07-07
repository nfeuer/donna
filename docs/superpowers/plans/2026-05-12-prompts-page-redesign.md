# Prompts Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Prompts page from a flat file list + stacked editor into a sidebar + main panel layout with a stats welcome dashboard.

**Architecture:** Sidebar (260px) with grouped, collapsible file tree + main panel that shows either a stats welcome view or the Monaco editor. Backend adds a `/admin/prompts/stats` endpoint that cross-references config files and invocation_log.

**Tech Stack:** React 18, TypeScript, CSS Modules, Radix primitives, Monaco editor, FastAPI, SQLite (aiosqlite), PyYAML

---

### Task 1: Backend — Prompt stats endpoint

**Files:**
- Modify: `src/donna/api/routes/admin_config.py`
- Create: `tests/unit/test_admin_prompts_stats.py`

- [ ] **Step 1: Write the test for prompt stats endpoint**

Create `tests/unit/test_admin_prompts_stats.py`:

```python
"""Tests for GET /admin/prompts/stats endpoint."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _prompts_dir(tmp_path: Path) -> Path:
    """Create a minimal prompts/ tree."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "parse_task.md").write_text("# Parse Task\n{{ raw_text }}")
    (prompts / "classify_priority.md").write_text("# Classify\n{{ task_title }}")
    chat = prompts / "chat"
    chat.mkdir()
    (chat / "chat_respond.md").write_text("# Respond")
    return prompts


@pytest.fixture
def _config_dir(tmp_path: Path) -> Path:
    """Create minimal config/ YAML stubs."""
    config = tmp_path / "config"
    config.mkdir()

    (config / "task_types.yaml").write_text(
        """task_types:
  parse_task:
    model: parser
    prompt_template: prompts/parse_task.md
    output_schema: schemas/task_parse_output.json
    tools: []
  classify_priority:
    model: parser
    prompt_template: prompts/classify_priority.md
    output_schema: schemas/priority_output.json
    tools: [task_db_read]
"""
    )

    (config / "donna_models.yaml").write_text(
        """models:
  parser:
    provider: anthropic
    model: claude-sonnet-4-6
  local_parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q4_K_M
routing:
  parse_task:
    model: parser
  classify_priority:
    model: parser
"""
    )

    (config / "agents.yaml").write_text(
        """agents:
  pm:
    enabled: true
    timeout_seconds: 300
    autonomy: medium
    allowed_tools: [task_db_read, task_db_write]
"""
    )

    return config


def test_prompt_stats_returns_shape(
    _prompts_dir: Path, _config_dir: Path, tmp_path: Path,
) -> None:
    """Verify response includes all expected top-level keys."""
    from donna.api.routes.admin_config import _build_prompt_stats

    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts={},
    )
    assert stats["total"] == 3
    assert "chat" in stats["by_folder"]
    assert stats["by_folder"]["chat"] == 1
    assert stats["by_folder"]["root"] == 2
    assert isinstance(stats["most_invoked"], list)
    assert isinstance(stats["agent_coverage"], list)
    assert isinstance(stats["model_routing"], dict)
    assert isinstance(stats["recently_modified"], list)
    assert isinstance(stats["unused"], list)


def test_prompt_stats_invocation_ranking(
    _prompts_dir: Path, _config_dir: Path,
) -> None:
    """most_invoked should be sorted descending by invocation count."""
    from donna.api.routes.admin_config import _build_prompt_stats

    counts = {
        "parse_task": {"invocations": 100, "cost_usd": 0.50},
        "classify_priority": {"invocations": 200, "cost_usd": 1.00},
    }
    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts=counts,
    )
    assert len(stats["most_invoked"]) == 2
    assert stats["most_invoked"][0]["task_type"] == "classify_priority"
    assert stats["most_invoked"][0]["invocations"] == 200


def test_prompt_stats_unused_detection(
    _prompts_dir: Path, _config_dir: Path,
) -> None:
    """Prompts with no matching task_type or zero invocations should appear in unused."""
    from donna.api.routes.admin_config import _build_prompt_stats

    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts={},
    )
    # chat_respond.md has no task_type mapping → unused
    assert "chat/chat_respond.md" in stats["unused"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/donna/donna && python3 -m pytest tests/unit/test_admin_prompts_stats.py -v`
Expected: FAIL — `_build_prompt_stats` does not exist yet.

- [ ] **Step 3: Implement `_build_prompt_stats` and the endpoint**

Add to `src/donna/api/routes/admin_config.py`, after the existing `list_prompts` endpoint:

```python
def _build_prompt_stats(
    *,
    prompts_dir: Path,
    config_dir: Path,
    invocation_counts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build prompt stats from config files and invocation counts.

    Pure function — all I/O (DB queries) happens in the caller.
    """
    # Enumerate prompts
    all_prompts: list[dict[str, Any]] = []
    for path in sorted(prompts_dir.rglob("*.md")):
        rel = str(path.relative_to(prompts_dir))
        stat = path.stat()
        all_prompts.append({"name": rel, "modified": stat.st_mtime})

    # Folder breakdown
    by_folder: dict[str, int] = {}
    for p in all_prompts:
        folder = str(Path(p["name"]).parent)
        key = "root" if folder == "." else folder
        by_folder[key] = by_folder.get(key, 0) + 1

    # Load task_types.yaml — map prompt filename → task_type metadata
    task_types_cfg = _load_yaml(config_dir / "task_types.yaml").get("task_types", {})
    prompt_to_task: dict[str, dict[str, str]] = {}
    for tt_name, tt_cfg in task_types_cfg.items():
        tpl = tt_cfg.get("prompt_template", "")
        # prompt_template is like "prompts/parse_task.md" → strip "prompts/" prefix
        rel_name = tpl.removeprefix("prompts/") if tpl.startswith("prompts/") else tpl
        if rel_name:
            prompt_to_task[rel_name] = {
                "task_type": tt_name,
                "model": tt_cfg.get("model", ""),
                "output_schema": tt_cfg.get("output_schema", ""),
            }

    # Model routing — load donna_models.yaml
    models_cfg = _load_yaml(config_dir / "donna_models.yaml")
    routing_cfg = models_cfg.get("routing", {})
    model_counts: dict[str, int] = {}
    for tt_name in prompt_to_task.values():
        route = routing_cfg.get(tt_name["task_type"], {})
        model_alias = route.get("model", tt_name["model"]) if isinstance(route, dict) else tt_name["model"]
        model_counts[model_alias] = model_counts.get(model_alias, 0) + 1

    # Agent coverage — which agents use which prompts
    agents_cfg = _load_yaml(config_dir / "agents.yaml").get("agents", {})
    # Reuse the agent→task_type map logic from admin_agents
    agent_task_map: dict[str, list[str]] = {}
    known_map: dict[str, list[str]] = {
        "pm": ["parse_task", "parse_task_local", "classify_priority", "dedup_check", "task_decompose"],
        "scheduler": ["generate_reminder"],
        "research": ["prep_research"],
        "coding": [],
        "challenger": ["challenge_task"],
        "communication": ["generate_nudge", "generate_digest", "generate_weekly_digest"],
    }
    for agent_name, agent_cfg in agents_cfg.items():
        agent_tools = set(agent_cfg.get("allowed_tools", []))
        mapped = set(known_map.get(agent_name, []))
        for tt_name, tt_cfg in task_types_cfg.items():
            tt_tools = set(tt_cfg.get("tools", []))
            if tt_tools and tt_tools & agent_tools:
                mapped.add(tt_name)
        agent_task_map[agent_name] = sorted(mapped)

    # Invert: prompt → agents
    prompt_agents: dict[str, list[str]] = {}
    for prompt_name, meta in prompt_to_task.items():
        tt = meta["task_type"]
        agents = [a for a, tts in agent_task_map.items() if tt in tts]
        if agents:
            prompt_agents[prompt_name] = sorted(agents)

    agent_coverage = sorted(
        [{"prompt": k, "agents": v} for k, v in prompt_agents.items()],
        key=lambda x: len(x["agents"]),
        reverse=True,
    )

    # Most invoked
    most_invoked = []
    for prompt_name, meta in prompt_to_task.items():
        tt = meta["task_type"]
        counts = invocation_counts.get(tt, {})
        if counts.get("invocations", 0) > 0:
            most_invoked.append({
                "prompt": prompt_name,
                "task_type": tt,
                "invocations": counts["invocations"],
                "cost_usd": round(counts.get("cost_usd", 0), 4),
            })
    most_invoked.sort(key=lambda x: x["invocations"], reverse=True)

    # Recently modified (top 3)
    recently_modified = sorted(all_prompts, key=lambda x: x["modified"], reverse=True)[:3]

    # Unused: prompts with no task_type mapping, or zero invocations
    mapped_prompts = set(prompt_to_task.keys())
    unused = []
    for p in all_prompts:
        name = p["name"]
        if name not in mapped_prompts:
            unused.append(name)
        elif prompt_to_task[name]["task_type"] not in invocation_counts:
            unused.append(name)

    return {
        "total": len(all_prompts),
        "by_folder": by_folder,
        "most_invoked": most_invoked[:10],
        "agent_coverage": agent_coverage,
        "model_routing": model_counts,
        "recently_modified": recently_modified,
        "unused": unused,
    }


@router.get("/prompts/stats")
async def get_prompt_stats(request: Request) -> dict[str, Any]:
    """Prompt usage stats for the welcome dashboard."""
    project_root = _get_project_root(request)
    prompts_dir = project_root / "prompts"
    config_dir = _get_config_dir(request)

    # Query invocation counts per task_type
    invocation_counts: dict[str, dict[str, Any]] = {}
    try:
        conn = request.app.state.db.connection
        cursor = await conn.execute(
            """SELECT task_type, COUNT(*), COALESCE(SUM(cost_usd), 0)
               FROM invocation_log
               GROUP BY task_type"""
        )
        for row in await cursor.fetchall():
            invocation_counts[row[0]] = {
                "invocations": row[1],
                "cost_usd": float(row[2]),
            }
    except Exception:
        pass

    return _build_prompt_stats(
        prompts_dir=prompts_dir,
        config_dir=config_dir,
        invocation_counts=invocation_counts,
    )
```

**Important:** This endpoint MUST be registered before the `GET /prompts/{filename:path}` route, otherwise FastAPI will treat `stats` as a filename path parameter. Move it directly after `list_prompts` and before `get_prompt`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/donna/donna && python3 -m pytest tests/unit/test_admin_prompts_stats.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/admin_config.py tests/unit/test_admin_prompts_stats.py
git commit -m "feat(api): add GET /admin/prompts/stats endpoint for prompt dashboard"
```

---

### Task 2: Backend — Enrich prompt detail with metadata

**Files:**
- Modify: `src/donna/api/routes/admin_config.py`

- [ ] **Step 1: Add metadata enrichment to `get_prompt`**

In `src/donna/api/routes/admin_config.py`, modify the `get_prompt` endpoint to add `task_type`, `model_alias`, and `output_schema` fields. After the `content = path.read_text(...)` line, add:

```python
    # Reverse-lookup: which task_type uses this prompt?
    config_dir = _get_config_dir(request)
    task_types_cfg = _load_yaml(config_dir / "task_types.yaml").get("task_types", {})
    task_type = None
    model_alias = None
    output_schema = None
    for tt_name, tt_cfg in task_types_cfg.items():
        tpl = tt_cfg.get("prompt_template", "")
        rel_name = tpl.removeprefix("prompts/") if tpl.startswith("prompts/") else tpl
        if rel_name == filename:
            task_type = tt_name
            model_alias = tt_cfg.get("model")
            output_schema = tt_cfg.get("output_schema")
            break
```

Then update the return dict to include the new fields:

```python
    return {
        "name": filename,
        "content": content,
        "size_bytes": path.stat().st_size,
        "modified": path.stat().st_mtime,
        "task_type": task_type,
        "model_alias": model_alias,
        "output_schema": output_schema,
    }
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /mnt/donna/donna && python3 -m pytest tests/unit/ -m "not slow and not llm" --tb=short -q`

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/routes/admin_config.py
git commit -m "feat(api): enrich prompt detail with task_type, model, and schema metadata"
```

---

### Task 3: Frontend API client — prompt stats

**Files:**
- Create: `donna-ui/src/api/promptStats.ts`
- Modify: `donna-ui/src/api/configs.ts`

- [ ] **Step 1: Create the prompt stats API client**

Create `donna-ui/src/api/promptStats.ts`:

```typescript
import client from "./client";

export interface PromptInvocationStat {
  prompt: string;
  task_type: string;
  invocations: number;
  cost_usd: number;
}

export interface PromptAgentCoverage {
  prompt: string;
  agents: string[];
}

export interface PromptRecentlyModified {
  name: string;
  modified: number;
}

export interface PromptStats {
  total: number;
  by_folder: Record<string, number>;
  most_invoked: PromptInvocationStat[];
  agent_coverage: PromptAgentCoverage[];
  model_routing: Record<string, number>;
  recently_modified: PromptRecentlyModified[];
  unused: string[];
}

export async function fetchPromptStats(): Promise<PromptStats> {
  const { data } = await client.get("/admin/prompts/stats");
  return data;
}
```

- [ ] **Step 2: Update `PromptContent` type in `configs.ts`**

In `donna-ui/src/api/configs.ts`, update the `PromptContent` interface to include the new metadata fields:

```typescript
export interface PromptContent {
  name: string;
  content: string;
  size_bytes: number;
  modified: number;
  task_type: string | null;
  model_alias: string | null;
  output_schema: string | null;
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /mnt/donna/donna/donna-ui && npx tsc --noEmit 2>&1 | grep -v axe-core`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/api/promptStats.ts donna-ui/src/api/configs.ts
git commit -m "feat(ui): add prompt stats API client and enrich PromptContent type"
```

---

### Task 4: Frontend — Prompt sidebar component

**Files:**
- Create: `donna-ui/src/pages/Prompts/PromptSidebar.tsx`
- Create: `donna-ui/src/pages/Prompts/PromptSidebar.module.css`

- [ ] **Step 1: Create the sidebar CSS**

Create `donna-ui/src/pages/Prompts/PromptSidebar.module.css`:

```css
.root {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-2);
  align-self: start;
  position: sticky;
  top: var(--space-4);
  max-height: calc(100vh - var(--space-6));
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.title {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  padding: var(--space-2) var(--space-3) 0;
}

.search {
  margin: 0 var(--space-2);
  font-size: var(--text-label);
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.groupHeader {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  background: transparent;
  border: 0;
  color: var(--color-text);
  font-family: var(--font-body);
  font-size: var(--text-label);
  letter-spacing: var(--tracking-wide);
  text-transform: uppercase;
  padding: var(--space-2) 0;
  cursor: pointer;
  text-align: left;
}
.groupHeader:hover { color: var(--color-accent); }

.chevron {
  display: inline-flex;
  color: var(--color-text-dim);
}

.groupLabel { flex: 1; }

.groupCount {
  color: var(--color-text-dim);
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
}

.children {
  list-style: none;
  margin: 0;
  padding: 0 0 var(--space-2) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.separator {
  border: 0;
  border-top: 1px solid var(--color-border);
  margin: var(--space-2) 0;
}

.fileItem {
  display: block;
  padding: 5px 8px 5px var(--space-3);
  border-left: 2px solid transparent;
  border-radius: var(--radius-control);
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-dim);
  text-decoration: none;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fileItem:hover {
  background: var(--color-accent-soft);
  color: var(--color-text);
}
.fileItemActive {
  border-left-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-text);
}

@media (max-width: 900px) {
  .root {
    position: static;
    max-height: 220px;
  }
}
```

- [ ] **Step 2: Create the sidebar component**

Create `donna-ui/src/pages/Prompts/PromptSidebar.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Input } from "../../primitives/Input";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import { fetchPrompts, type PromptFile } from "../../api/configs";
import styles from "./PromptSidebar.module.css";

interface Props {
  selected: string | null;
}

interface FolderGroup {
  folder: string;
  files: PromptFile[];
}

function groupByFolder(files: PromptFile[]): { groups: FolderGroup[]; root: PromptFile[] } {
  const folders = new Map<string, PromptFile[]>();
  const root: PromptFile[] = [];

  for (const f of files) {
    const sep = f.name.lastIndexOf("/");
    if (sep === -1) {
      root.push(f);
    } else {
      const folder = f.name.slice(0, sep);
      const existing = folders.get(folder);
      if (existing) existing.push(f);
      else folders.set(folder, [f]);
    }
  }

  const groups = Array.from(folders.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([folder, files]) => ({ folder, files }));

  return { groups, root };
}

function stripMd(name: string): string {
  const base = name.includes("/") ? name.slice(name.lastIndexOf("/") + 1) : name;
  return base.replace(/\.md$/, "");
}

export default function PromptSidebar({ selected }: Props) {
  const [files, setFiles] = useState<PromptFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLoading(true);
    fetchPrompts()
      .then(setFiles)
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    if (!search) return files;
    const q = search.toLowerCase();
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, search]);

  const { groups, root } = useMemo(() => groupByFolder(filtered), [filtered]);

  const toggleGroup = (folder: string) => {
    const next = new Set(collapsed);
    if (next.has(folder)) next.delete(folder);
    else next.add(folder);
    setCollapsed(next);
  };

  if (loading) {
    return (
      <aside className={styles.root} aria-label="Prompt templates">
        <div className={styles.title}>Prompt Templates</div>
        <Skeleton height={28} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
      </aside>
    );
  }

  return (
    <aside className={styles.root} aria-label="Prompt templates">
      <div className={styles.title}>Prompt Templates</div>
      <Input
        type="search"
        placeholder="Filter…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className={styles.search}
        aria-label="Filter prompt templates"
      />

      <ul className={styles.list}>
        {groups.map(({ folder, files: groupFiles }) => {
          const isCollapsed = collapsed.has(folder);
          return (
            <li key={folder}>
              <button
                type="button"
                className={styles.groupHeader}
                onClick={() => toggleGroup(folder)}
                aria-expanded={!isCollapsed}
              >
                <span className={styles.chevron} aria-hidden="true">
                  {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                </span>
                <span className={styles.groupLabel}>{folder}</span>
                <span className={styles.groupCount}>{groupFiles.length}</span>
              </button>
              {!isCollapsed && (
                <ul className={styles.children}>
                  {groupFiles.map((f) => (
                    <li key={f.name}>
                      <Link
                        to={`/prompts/${f.name}`}
                        className={cn(styles.fileItem, f.name === selected && styles.fileItemActive)}
                        aria-current={f.name === selected ? "page" : undefined}
                      >
                        {stripMd(f.name)}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}

        {groups.length > 0 && root.length > 0 && (
          <li><hr className={styles.separator} /></li>
        )}

        {root.map((f) => (
          <li key={f.name}>
            <Link
              to={`/prompts/${f.name}`}
              className={cn(styles.fileItem, f.name === selected && styles.fileItemActive)}
              aria-current={f.name === selected ? "page" : undefined}
            >
              {stripMd(f.name)}
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /mnt/donna/donna/donna-ui && npx tsc --noEmit 2>&1 | grep -v axe-core`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Prompts/PromptSidebar.tsx donna-ui/src/pages/Prompts/PromptSidebar.module.css
git commit -m "feat(ui): add PromptSidebar with grouped file tree and search"
```

---

### Task 5: Frontend — Prompt welcome dashboard

**Files:**
- Create: `donna-ui/src/pages/Prompts/PromptWelcome.tsx`
- Create: `donna-ui/src/pages/Prompts/PromptWelcome.module.css`

- [ ] **Step 1: Create the welcome CSS**

Create `donna-ui/src/pages/Prompts/PromptWelcome.module.css`:

```css
.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.grid > .fullWidth {
  grid-column: 1 / -1;
}

@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}

/* Staggered card rise — matches Dashboard.module.css */
.grid > * {
  opacity: 1;
  transform: none;
}

.root[data-entered="false"] .grid > * {
  opacity: 0;
  transform: translateY(8px);
}

.root[data-entered="true"] .grid > * {
  animation: cardRise var(--duration-base) var(--ease-out) both;
}

.root[data-entered="true"] .grid > *:nth-child(1) { animation-delay: 0ms; }
.root[data-entered="true"] .grid > *:nth-child(2) { animation-delay: 50ms; }
.root[data-entered="true"] .grid > *:nth-child(3) { animation-delay: 100ms; }
.root[data-entered="true"] .grid > *:nth-child(4) { animation-delay: 150ms; }
.root[data-entered="true"] .grid > *:nth-child(5) { animation-delay: 200ms; }

@keyframes cardRise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .root[data-entered="false"] .grid > *,
  .root[data-entered="true"] .grid > * {
    opacity: 1;
    transform: none;
    animation: none;
  }
}

.sectionTitle {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-2);
}

.rankedList {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.rankedItem {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  cursor: pointer;
  text-decoration: none;
  color: var(--color-text);
  transition: background var(--duration-fast) var(--ease-out);
}
.rankedItem:hover { background: var(--color-accent-soft); }

.rankedRank {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-muted);
  width: 20px;
  text-align: right;
}

.rankedName {
  font-family: var(--font-mono);
  font-size: var(--text-body);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rankedMeta {
  font-size: var(--text-label);
  color: var(--color-text-dim);
  white-space: nowrap;
}

.pillList {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.folderBreakdown {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  font-size: var(--text-body);
  color: var(--color-text-dim);
}

.unusedList {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.unusedItem {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-muted);
  padding: var(--space-1) 0;
}
```

- [ ] **Step 2: Create the welcome component**

Create `donna-ui/src/pages/Prompts/PromptWelcome.tsx`:

```tsx
import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { Card, CardHeader, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Skeleton } from "../../primitives/Skeleton";
import { Stat } from "../../primitives/Stat";
import { ChartCard, type ChartCardStat } from "../../charts";
import { BarChart } from "../../charts";
import { fetchPromptStats, type PromptStats } from "../../api/promptStats";
import styles from "./PromptWelcome.module.css";

dayjs.extend(relativeTime);

export default function PromptWelcome() {
  const [stats, setStats] = useState<PromptStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [entered, setEntered] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await fetchPromptStats());
    } catch {
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  if (loading) {
    return (
      <div className={styles.root}>
        <div className={styles.grid}>
          <Card><Skeleton height={120} /></Card>
          <Card><Skeleton height={120} /></Card>
          <Card><Skeleton height={160} /></Card>
          <Card><Skeleton height={160} /></Card>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const folderStats: ChartCardStat[] = Object.entries(stats.by_folder).map(
    ([folder, count]) => ({ label: folder, value: String(count) }),
  );

  const modelChartData = Object.entries(stats.model_routing).map(
    ([model, count]) => ({ model, count }),
  );

  return (
    <div className={styles.root} data-entered={entered ? "true" : "false"}>
      <div className={styles.grid}>
        {/* Overview */}
        <ChartCard
          eyebrow="Prompt Templates"
          metric={String(stats.total)}
          stats={folderStats}
          loading={false}
        >
          <div className={styles.folderBreakdown}>
            {Object.entries(stats.by_folder).map(([folder, count]) => (
              <span key={folder}>{count} {folder}</span>
            ))}
          </div>
        </ChartCard>

        {/* Local vs Cloud */}
        <ChartCard
          eyebrow="Model Routing"
          metric={String(Object.values(stats.model_routing).reduce((a, b) => a + b, 0))}
          metricSuffix=" routed"
          chart={
            modelChartData.length > 0 ? (
              <BarChart
                data={modelChartData}
                series={[{ dataKey: "count", name: "Prompts" }]}
                categoryKey="model"
                orientation="vertical"
                categoryWidth={100}
                height={100}
                ariaLabel="Prompt count by model"
              />
            ) : undefined
          }
          loading={false}
        />

        {/* Most invoked */}
        <Card>
          <CardHeader><CardTitle>Most Invoked</CardTitle></CardHeader>
          {stats.most_invoked.length === 0 ? (
            <div className={styles.rankedMeta} style={{ padding: "var(--space-3)" }}>
              No invocations recorded yet.
            </div>
          ) : (
            <ul className={styles.rankedList}>
              {stats.most_invoked.slice(0, 5).map((item, i) => (
                <li key={item.prompt}>
                  <Link to={`/prompts/${item.prompt}`} className={styles.rankedItem}>
                    <span className={styles.rankedRank}>{i + 1}</span>
                    <span className={styles.rankedName}>{item.prompt.replace(/\.md$/, "")}</span>
                    <span className={styles.rankedMeta}>
                      {item.invocations.toLocaleString()} calls · ${item.cost_usd.toFixed(2)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Agent coverage */}
        <Card>
          <CardHeader><CardTitle>Agent Coverage</CardTitle></CardHeader>
          {stats.agent_coverage.length === 0 ? (
            <div className={styles.rankedMeta} style={{ padding: "var(--space-3)" }}>
              No agent mappings found.
            </div>
          ) : (
            <ul className={styles.rankedList}>
              {stats.agent_coverage.map((item) => (
                <li key={item.prompt}>
                  <Link to={`/prompts/${item.prompt}`} className={styles.rankedItem}>
                    <span className={styles.rankedName}>{item.prompt.replace(/\.md$/, "")}</span>
                    <div className={styles.pillList}>
                      {item.agents.map((a) => (
                        <Pill key={a} variant="muted">{a}</Pill>
                      ))}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Recently modified + Unused */}
        <div className={styles.fullWidth}>
          <Card>
            <CardHeader><CardTitle>Activity</CardTitle></CardHeader>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", padding: "0 var(--space-3) var(--space-3)" }}>
              <div>
                <div className={styles.sectionTitle}>Recently Modified</div>
                <ul className={styles.rankedList}>
                  {stats.recently_modified.map((item) => (
                    <li key={item.name}>
                      <Link to={`/prompts/${item.name}`} className={styles.rankedItem}>
                        <span className={styles.rankedName}>{item.name.replace(/\.md$/, "")}</span>
                        <span className={styles.rankedMeta}>{dayjs(item.modified * 1000).fromNow()}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className={styles.sectionTitle}>
                  Unused {stats.unused.length > 0 && <Pill variant="warning">{stats.unused.length}</Pill>}
                </div>
                {stats.unused.length === 0 ? (
                  <div className={styles.rankedMeta}>All prompts are in use.</div>
                ) : (
                  <ul className={styles.unusedList}>
                    {stats.unused.map((name) => (
                      <li key={name} className={styles.unusedItem}>
                        {name.replace(/\.md$/, "")}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /mnt/donna/donna/donna-ui && npx tsc --noEmit 2>&1 | grep -v axe-core`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Prompts/PromptWelcome.tsx donna-ui/src/pages/Prompts/PromptWelcome.module.css
git commit -m "feat(ui): add PromptWelcome stats dashboard for empty state"
```

---

### Task 6: Frontend — Rewire page layout and clean up old components

**Files:**
- Modify: `donna-ui/src/pages/Prompts/index.tsx`
- Modify: `donna-ui/src/pages/Prompts/Prompts.module.css`
- Modify: `donna-ui/src/pages/Prompts/PromptEditor.tsx`
- Delete: `donna-ui/src/pages/Prompts/PromptsList.tsx`
- Delete: `donna-ui/src/pages/Prompts/PromptFileList.tsx`

- [ ] **Step 1: Replace `Prompts.module.css` with grid layout**

Rewrite `donna-ui/src/pages/Prompts/Prompts.module.css`:

```css
.root {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--space-4);
  min-height: 100%;
  font-family: var(--font-body);
  color: var(--color-text);
}

.main {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.editorHeader {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3) var(--space-4);
}

.editorTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0;
}

.editorStatus {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.metaBar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-label);
  color: var(--color-text-muted);
}

.editorGrid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
}

@media (max-width: 900px) {
  .root { grid-template-columns: 1fr; }
  .editorHeader { flex-direction: column; align-items: flex-start; }
  .editorGrid { grid-template-columns: 1fr; }
}

.variableChips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.variableEmpty {
  color: var(--color-text-muted);
  font-size: var(--text-body);
}

.invalid { color: var(--color-error); font-size: var(--text-label); }
```

- [ ] **Step 2: Rewrite `index.tsx` as the sidebar+main layout**

Rewrite `donna-ui/src/pages/Prompts/index.tsx`:

```tsx
import { useParams } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import PromptSidebar from "./PromptSidebar";
import PromptEditor from "./PromptEditor";
import PromptWelcome from "./PromptWelcome";
import styles from "./Prompts.module.css";

export default function PromptsPage() {
  const { "*": splat } = useParams();
  const selected = splat || null;

  return (
    <div className={styles.root}>
      <PromptSidebar selected={selected} />
      <section className={styles.main}>
        {selected ? (
          <PromptEditor file={selected} />
        ) : (
          <>
            <PageHeader eyebrow="System" title="Prompts" />
            <PromptWelcome />
          </>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Simplify `PromptEditor.tsx` — remove file list and add metadata bar**

In `donna-ui/src/pages/Prompts/PromptEditor.tsx`:

1. Remove the `PromptFileList` import and its usage (line 11 and line 192).
2. Remove the `files`, `filesLoading`, `loadFiles` state and effects (lines 76-99). Also remove the `loadFiles()` call in `handleSave` (line 133).
3. Remove the `PageHeader` wrapping (the page-level header is now in `index.tsx`).
4. Add the metadata bar using `task_type`, `model_alias`, `output_schema` from the enriched response.

The updated component should look like:

```tsx
import { useState, useEffect, useCallback } from "react";
import { Save } from "lucide-react";
import Editor from "@monaco-editor/react";
import { toast } from "sonner";
import { Button } from "../../primitives/Button";
import { Pill } from "../../primitives/Pill";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";
import MarkdownPreview from "./MarkdownPreview";
import VariableInspector from "./VariableInspector";
import SaveDiffModal from "../Configs/SaveDiffModal";
import { fetchPrompt, savePrompt, type PromptContent } from "../../api/configs";
import styles from "./Prompts.module.css";

interface Props {
  file: string;
}

export default function PromptEditor({ file }: Props) {
  const filename = decodeURIComponent(file);

  const [meta, setMeta] = useState<PromptContent | null>(null);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"edit" | "preview" | "split">("split");
  const hasChanges = editedContent !== originalContent;

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setShowDiff(false);
    setContentLoading(true);
    fetchPrompt(filename)
      .then((d) => {
        if (cancelled) return;
        setMeta(d);
        setOriginalContent(d.content);
        setEditedContent(d.content);
      })
      .catch(() => {
        if (cancelled) return;
        setMeta(null);
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => { cancelled = true; };
  }, [filename]);

  const handleSave = useCallback(async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await savePrompt(filename, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      toast.success(`Saved ${filename}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [filename, editedContent]);

  const editorEl = (
    <Editor
      height="min(60vh, 560px)"
      language="markdown"
      theme={DONNA_MONACO_THEME}
      beforeMount={setupDonnaMonacoTheme}
      value={editedContent}
      onChange={(v) => setEditedContent(v ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
      }}
    />
  );

  return (
    <>
      <div className={styles.editorHeader}>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div className={styles.editorStatus} role="status" aria-live="polite">
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
          <Button
            variant="primary"
            size="sm"
            disabled={!hasChanges}
            onClick={() => setShowDiff(true)}
          >
            <Save size={14} /> Save
          </Button>
        </div>
      </div>

      {meta && (
        <div className={styles.metaBar}>
          <span>{(meta.size_bytes / 1024).toFixed(1)} KB</span>
          <span>Modified {new Date(meta.modified * 1000).toLocaleDateString()}</span>
          {meta.model_alias && <Pill variant="accent">{meta.model_alias}</Pill>}
          {meta.output_schema && <Pill variant="muted">{meta.output_schema}</Pill>}
        </div>
      )}

      <Tabs value={view} onValueChange={(v) => setView(v as typeof view)}>
        <TabsList>
          <TabsTrigger value="edit">Edit</TabsTrigger>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="split">Split</TabsTrigger>
        </TabsList>
        <TabsContent value="edit">{editorEl}</TabsContent>
        <TabsContent value="preview">
          <MarkdownPreview content={editedContent} />
        </TabsContent>
        <TabsContent value="split">
          <div className={styles.editorGrid}>
            {editorEl}
            <MarkdownPreview content={editedContent} />
          </div>
        </TabsContent>
      </Tabs>

      <VariableInspector content={editedContent} schemaPath={meta?.output_schema ?? null} />

      <SaveDiffModal
        open={showDiff}
        original={originalContent}
        modified={editedContent}
        filename={filename}
        saving={saving}
        onConfirm={handleSave}
        onCancel={() => setShowDiff(false)}
      />
    </>
  );
}
```

- [ ] **Step 4: Delete old components**

```bash
rm donna-ui/src/pages/Prompts/PromptsList.tsx donna-ui/src/pages/Prompts/PromptFileList.tsx
```

- [ ] **Step 5: Verify TypeScript compiles and build passes**

Run: `cd /mnt/donna/donna/donna-ui && npx tsc --noEmit 2>&1 | grep -v axe-core && npx vite build 2>&1 | tail -5`
Expected: No TS errors, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add -A donna-ui/src/pages/Prompts/
git commit -m "feat(ui): prompts page redesign — sidebar + stats welcome + metadata bar"
```

---

### Task 7: Visual verification and polish

**Files:** None new — this is testing and adjusting.

- [ ] **Step 1: Rebuild and restart containers**

```bash
cd /mnt/donna/donna/docker
docker compose -f donna-app.yml build donna-api
docker compose -f donna-app.yml up -d donna-api
docker compose -f donna-ui.yml build donna-ui
docker compose -f donna-ui.yml up -d donna-ui
```

- [ ] **Step 2: Verify the prompts page in browser**

Open the Donna UI in a browser. Check:
- Sidebar shows grouped folders (CHAT, ESCALATION, SKILLS, VAULT) with collapsible sections and root prompts below.
- Search filter narrows the file list.
- Empty state shows the stats welcome dashboard with all 5 cards.
- Clicking a prompt loads the editor in the main panel without page navigation.
- Metadata bar shows model and schema pills.
- Editor tabs (Edit/Preview/Split) work.
- Save flow works (edit content → Save button → diff modal → confirm).
- Responsive: narrow the window to verify single-column fallback.

- [ ] **Step 3: Fix any visual issues found during testing**

Adjust spacing, alignment, or token usage as needed. Every fix should use existing CSS custom properties from `tokens.css`.

- [ ] **Step 4: Run full test suite**

```bash
cd /mnt/donna/donna && python3 -m pytest tests/unit/ -m "not slow and not llm" --tb=short -q
cd /mnt/donna/donna/donna-ui && npx vite build
```

- [ ] **Step 5: Final commit if any polish changes were made**

```bash
git add -A
git commit -m "fix(ui): prompts page polish from visual review"
```
