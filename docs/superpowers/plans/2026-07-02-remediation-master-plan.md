# Donna Remediation — Master Plan (session sequencing)

**Source:** `AUDIT_REPORT_2026-07-02.md` (+ `.audit/*.md`). **Created:** 2026-07-02.
**Owner guidance driving this plan:**
- Tackle **security first**.
- For the **wiring** work, *design before cutting*. Understand original intent. Most wiring gaps exist because the author built features from a large spec but never strung the seams together. Fix the wiring properly. Remove a pathway **only** if its intent is carried through elsewhere, or the design direction was wrong.
- **Skip Google Calendar OAuth** — owner is setting up the permanent fix separately.

This is the top-level sequencing. Each session below is (or will be) a standalone plan document producing working, testable software on its own.

---

## Why this order

The audit's three themes are: (A) silent production failures, (B) the proactive core is built-but-unwired, (C) headline features are dead code. Security is orthogonal to all three and is the only dimension with a live, externally-reachable HIGH — so it goes first and independently. The wiring work (B/C) is sequenced after a **design pass** because the owner explicitly wants intent understood before any code is cut, and several wiring fixes touch the same files (`cli_wiring.py`, `server.py`, `models/router.py`) and so must be planned together to avoid merge thrash.

```
Session 1  Security hardening            ── independent, ship first
Session 2  Silent-failure surfacing      ── depends on nothing; highest value/effort
             (model-ID fix, dispatch_fallback_alert gaps, dependency self-health check)
Session 3  Wiring DESIGN doc             ── design only, no code; gates Sessions 4–6
Session 4  Proactive-loop wiring         ── "just wiring" cluster (4 prompts, heartbeats, supervision)
Session 5  LLM gateway + shadow mode     ── needs a keep-vs-simplify design decision (Session 3)
Session 6  State machine + CalendarSync  ── correctness-of-intent cluster
Session 7  Config/deploy hygiene         ── .dockerignore, lockfile-in-Docker, port binding, image pins
Session 8  Observability reconciliation  ── dashboards vs emitted events, contextvar binding
```

Sessions 1, 2, and 7 are independent and can run in any order / parallel sessions. Sessions 4–6 are gated by the Session 3 design doc. Session 8 is best last (it depends on which events Sessions 4–6 end up emitting).

---

## Session summaries

### Session 1 — Security hardening  *(plan: `2026-07-02-session-1-security-hardening.md`)*
Close the one externally-reachable HIGH (six unauthenticated `/admin` route modules) and reduce the blast radius (bind sensitive ports to loopback, tighten the OAuth-secret mount, weak Grafana default). Spec: §27/§28. **Executable now — no design ambiguity.**

### Session 2 — Silent-failure surfacing *(highest leverage in the repo)*
The audit's single most important runtime fact: the cloud model `claude-sonnet-4-6` is retired and 404s nightly, silently. Fix the model ID; route every degraded `except` through `dispatch_fallback_alert()` (starting with `memory/writer.py` and the five `except: pass` sites); add a **dependency self-health check** (ping model ID / Gmail / calendar on startup + periodically, alert to Discord on failure). *Calendar OAuth itself is out of scope per owner — but the self-health check that would have surfaced it is in scope.* Spec: §13, §14, conventions in CLAUDE.md.

### Session 3 — Wiring design doc  *(design only)*
Reconstruct the intended design for each build-but-unwired feature and decide, per feature, **wire-through vs simplify-with-intent-preserved**. Fed by three intent-reconstruction investigations (LLM gateway/shadow; proactive loops/heartbeats/supervision; state machine/calendar/config). Output: a design document with a target architecture and an explicit keep/wire/delete disposition per item, each justified against spec intent. **No code.** This is the "spend more time designing" deliverable.

### Session 4 — Proactive-loop wiring
The "just wiring" cluster (low risk, high value): construct and start the four proactive prompts, wire the two health-heartbeat writers, apply the existing supervision pattern to the ~15 background loops, and add the reminder parse-failure log. Almost entirely additive — completes seams the author left open.

### Session 5 — LLM gateway + shadow mode
Implements the Session-3 decision. Either route orchestrator `ModelRouter` calls through the gateway (making §26's choke point + preemption real) or make `ModelRouter` the acknowledged choke point and retire the dead internal-queue lane — **with §26 updated to match reality**. Same for shadow-mode wiring.

### Session 6 — State machine + CalendarSync correctness
Add the missing "complete/cancel from anywhere" transitions to `config/task_states.yaml` and force all writers through `transition_task_state` (remove the `update_task(status=)` bypass and the free-string API PATCH). Fix CalendarSync to fail **closed** on calendar read errors (matching the Scheduler's intent) instead of mass-unscheduling. Fix the two config traps.

### Session 7 — Config/deploy hygiene
`.dockerignore`; use `uv.lock` in Docker builds; split `sentence-transformers` to an optional extra (API image 9 GB → ~0.5 GB); `npm audit fix` in `donna-ui`; pin `ollama` + Grafana plugin; upgrade EOL Grafana/Loki; fix the fresh-machine deploy paths.

### Session 8 — Observability reconciliation
Regenerate Grafana dashboards against actually-emitted events (or add the missing `task.*`/`api.call.*` event fields); bind the logging contextvars at the three ingress points; add healthwatch to the promtail scrape; persist promtail positions.

---

## Cross-session file contention (plan around these)

| File | Touched by |
|---|---|
| `src/donna/cli_wiring.py` | Sessions 2, 4, 5 — **sequence, don't parallelize** |
| `src/donna/server.py` | Sessions 4 (heartbeats, supervision) |
| `src/donna/models/router.py` | Sessions 2 (self-health), 5 (gateway) |
| `config/donna_models.yaml` | Sessions 2 (model ID), 6 (model-tag mismatch) |
| `docker/*.yml` | Sessions 1 (ports/mounts), 7 (pins/paths) |
| `spec_v3.md` | Sessions 5 (§26), 6 (§5.2) — keep the spec in sync per CLAUDE.md |

Recommendation: run Sessions 1, 2, 7 first (independent), then 3→(4,5,6)→8.
