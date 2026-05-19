# Skill System — Archived Manual Wiring Steps

> These are the original Phase 1/2 manual wiring instructions from `docs/domain/skill-system.md` sections 4.1 and 4.2. They were superseded on 2026-04-21 when `assemble_skill_system()` in `src/donna/cli_wiring.py:300-470` automated the entire startup path. Preserved here for historical context only.

---

### 4.1 Wire `initialize_skill_system` into application startup

The startup hook `src/donna/skills/startup.py::initialize_skill_system(conn, skills_dir)` must be called once at application boot. It:
- Generates embeddings for any capability rows with `embedding IS NULL` (uses sentence-transformers).
- Loads any seed skills from `skills/` into the DB for capabilities that don't yet have a skill.
- Builds and returns a `ToolRegistry` populated with built-in tools (currently `web_fetch`; add more by extending `register_default_tools` in `src/donna/skills/tools/__init__.py`).

All operations are idempotent and cheap after the first run. **Capture the returned `ToolRegistry`** — the dispatcher wiring in §4.2 needs it.

**Where to add the call:** find the application startup hook. Look in `src/donna/server.py` (FastAPI app lifespan) or `src/donna/cli.py` (if there's a CLI that boots the service). Expected pattern:

```python
from pathlib import Path
from donna.skills.startup import initialize_skill_system

# Inside the startup/lifespan function, after the DB connection is established
# and BEFORE the dispatcher starts serving traffic:
skill_tool_registry = await initialize_skill_system(db_conn, Path("skills"))
# Store `skill_tool_registry` somewhere reachable from §4.2 (e.g., app.state).
```

Since `SkillSystemConfig` is not yet wired into any top-level config (see §3), there's no flag to check here. Either always call `initialize_skill_system` (it's idempotent and cheap) and decide whether to use the results in §4.2, or guard it with a local boolean constant while we defer config wiring.

### 4.2 Construct the dispatcher + skill execution stack

The Phase 1 dispatcher (`src/donna/orchestrator/dispatcher.py::AgentDispatcher`) gained three optional parameters: `skill_executor`, `skill_database`, `skill_routing_enabled`. Phase 2 added `ToolRegistry`, `TriageAgent`, and `SkillRunRepository` which the executor needs to actually do anything useful. To activate the skill shadow path, the caller that constructs the dispatcher must wire all of this together.

**Where to change:** find the code that instantiates `AgentDispatcher(...)` (likely in `src/donna/server.py` or a startup initializer). Update to:

```python
# Capabilities layer
from donna.capabilities.registry import CapabilityRegistry
from donna.capabilities.matcher import CapabilityMatcher
from donna.capabilities.input_extractor import LocalLLMInputExtractor

# Skills layer
from donna.skills.executor import SkillExecutor
from donna.skills.database import SkillDatabase
from donna.skills.run_persistence import SkillRunRepository
from donna.skills.triage import TriageAgent

# Existing agent
from donna.agents.challenger_agent import ChallengerAgent

# --- Capabilities ---
capability_registry = CapabilityRegistry(db_conn)
capability_matcher = CapabilityMatcher(capability_registry)
input_extractor = LocalLLMInputExtractor(model_router)

# --- Skills infrastructure ---
# skill_tool_registry comes from initialize_skill_system() in §4.1
triage = TriageAgent(model_router)
skill_run_repo = SkillRunRepository(db_conn)
skill_executor = SkillExecutor(
    model_router,
    tool_registry=skill_tool_registry,   # from §4.1
    triage=triage,
    run_repository=skill_run_repo,
)
skill_database = SkillDatabase(db_conn)

# --- Refactored challenger ---
challenger = ChallengerAgent(matcher=capability_matcher, input_extractor=input_extractor)

# --- Dispatcher ---
dispatcher = AgentDispatcher(
    agents={..., "challenger": challenger, ...},
    tool_registry=tool_registry,               # existing agent tool registry, NOT the skill one
    router=model_router,
    db=db,
    project_root=project_root,
    activity_listener=activity_listener,
    skill_executor=skill_executor,             # NEW in Phase 1
    skill_database=skill_database,             # NEW in Phase 1
    skill_routing_enabled=True,                # flip this to activate
)
```

**Important:** the agent `tool_registry` (for PM, prep, scheduler agents — existing) and the skill `tool_registry` (for the skill executor — new, returned from `initialize_skill_system`) are **different objects**. Don't conflate them. The skill executor only knows about tools registered in the skill ToolRegistry; the agents only see tools in the agent ToolRegistry.
