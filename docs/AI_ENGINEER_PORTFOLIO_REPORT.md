# Donna as an AI-Engineer Portfolio: What to Add & Expand

**For:** Nick · **Date:** 2026-07-03 · **Purpose:** concrete, prioritized moves to make Donna demonstrate the skills that AI-Engineer / LLM-Application-Engineer hiring managers actually screen for.

Grounded in: the 2026-07-02 repository audit (`AUDIT_REPORT_2026-07-02.md`, esp. the *skills/portfolio* dimension `.audit/skills.md`), the overnight wiring remediation, and current AI-engineering hiring signals (§5 below).

---

## TL;DR — the three highest-leverage moves

1. **Build a real evaluation discipline.** Evals are *the* dividing line between "wired an API to an LLM" and "production LLM engineer," and they are Donna's single weakest area. You already have the scaffolding (a `donna eval` harness + tiered fixtures) — expanding it into a first-class, CI-wired system with a quality dashboard is the highest signal-per-hour work in the whole project.
2. **Own the AI-assisted method as a feature.** The biggest thing a skeptical reviewer flags is "how much of this did the author actually drive?" You have unusually strong evidence that you drove it well (the Fable adversarial-critique program; this audit-and-remediate loop). Foregrounding *how* you engineer with AI is itself a sought-after 2026 skill — and it's a few hours of writing, not code.
3. **Make the sophisticated parts legible and demonstrably working.** You just spent this session closing the "is it all actually wired?" gap (proactive loops, state machine, gateway honesty, calendar fail-closed, six security holes). That removes the worst portfolio liability. Now *show* the hard parts working — don't let them stay buried in 64k lines.

---

## 1. What Donna already proves (lead with these — they're genuinely strong)

A hiring manager skimming this repo should be walked straight to these. Per the audit, they're real, not scaffolded:

- **Multi-model routing with local-first + cloud escalation by confidence** (`models/router.py`, `orchestrator/input_parser.py`). Local Ollama parse, escalate to Claude only when confidence < 0.7. This is exactly the cost/quality tradeoff engineering that senior AI roles want.
- **Cost/budget enforcement as a structural invariant** — `complete()` refuses to make an unlogged billed call; daily/monthly caps enforced pre-call; every invocation logged with tokens/cost. Production LLMOps, not a toy.
- **Structured output everywhere** — JSON-schema-validated LLM outputs with typed retry (`models/validation.py`), a config-driven prompt+schema registry (`task_types.yaml`).
- **An agentic "skills" system with real safety gates** — shadow mode, trust-tier promotion, evolution gates. The audit called this "the product's central bet"; it's the most impressive machinery in the repo.
- **Semantic memory / RAG** with sqlite-vec, heading-aware chunking, content-hash dedup (`memory/`) — the audit's most-praised subsystem.
- **An operated observability stack** (Loki/Promtail/Grafana, 9 containers, 8 days zero-restart) — most application engineers can write compose files; few *run* the stack.
- **Engineering process maturity** — a maintained spec with a drift ledger, 25 slice briefs, dated ADRs, and a *self-adversarial* design-critique program. This reads as staff-level.

**Packaging note:** your README currently leads with a feature list. Re-lead with the *AI-engineering capabilities* above — that's what the target audience screens for.

---

## 2. The gaps AI-eng interviewers will probe (ranked by how much they matter)

| # | Gap | Why it matters to the target role |
|---|-----|-----------------------------------|
| 1 | **Evals are thin** — the harness crashes on the adversarial tier; no fixtures for the highest-traffic prompts (challenger_parse, reply_intent, chat-intent, time_intent); not run in CI | Eval-driven development is the #1 differentiator in 2026 AI-eng hiring |
| 2 | **RAG retrieval quality is unmeasured** — memory search exists, but there's no recall@k / MRR / labeled query set | RAG quality measurement is a top-3 screened skill |
| 3 | **"Is it actually wired?"** — several headline features were dead code (gateway preemption, shadow wiring, silent autowrite) | *Largely fixed this session* — but verify none remain before showcasing |
| 4 | **AI-assisted development is under-disclosed** — "solo, 1000+ commits" invites LOC-discounting | Skeptics will probe drive-vs-accept; owning it flips the flag |
| 5 | **Agentic system is buried** — the sophisticated shadow/trust/evolution loop has no demo or writeup | Agentic design is a headline 2026 competency |
| 6 | **Prompt-injection surface** — forwarded-email text flows raw into a parse prompt that gates the prep agent's tools | Guardrails/safety is increasingly screened |

---

## 3. Prioritized additions & expansions

Each item: **what to build → what skill it demonstrates → effort/leverage.** Ordered by leverage.

### A. Turn the eval harness into a first-class, CI-wired system ★ highest leverage
**Build:** (1) fix the `donna eval` crash on `pass_gate: null`; (2) add fixture tiers for the *front-door* prompts that actually carry traffic — `challenger_parse` (every #donna-tasks message), `reply_intent`, chat-intent classification, and the `time_intent`/timezone cases (the exact bug class the `fix/parse-tz-time-intent` branch chased); (3) run evals in CI on any change to `prompts/` or `schemas/`, failing the build on regression against a pinned gate; (4) emit a small eval-results artifact / Grafana panel showing pass-rate and per-tier scores over time.
**Demonstrates:** eval-driven development, prompt-engineering rigor, treating LLM outputs as measurable, CI discipline for non-deterministic systems. **This is the single most convincing thing you can add.**
**Effort:** medium (the harness exists). **Leverage:** very high.
**Interview soundbite:** "I gate prompt changes on an eval suite in CI — here's the dashboard, here's a PR the eval caught."

### B. A RAG retrieval-quality eval ★
**Build:** a small hand-labeled query→expected-chunk set over your vault/memory; compute recall@k, MRR, and answer-groundedness; a short report comparing embedding/chunking choices (you already have the knobs: `chunk_overlap`, `min_score`). Wire it as a second eval track.
**Demonstrates:** RAG competence beyond "I called an embedding API" — retrieval quality is what separates real RAG engineers.
**Effort:** medium. **Leverage:** high.

### C. Showcase the agentic skills loop (demo + writeup) ★
**Build:** a focused case study — "How Donna learns a skill safely": a captured example flowing shadow-mode → divergence check → trust-gate → promotion, with the trust math and what happens on divergence. A 60-second asciinema/video + a `docs/` page.
**Demonstrates:** agentic system design *with* safety gates — a headline 2026 skill, and your most sophisticated existing machinery. It's built; it just needs to be legible.
**Effort:** low-medium (mostly writing). **Leverage:** high.

### D. Make cost/latency engineering visible
**Build:** (1) fix the dead Grafana cost/task dashboards (the audit found they query event types the code never emits — regenerate against actual events); (2) a short "routing economics" writeup: $ saved by local-first, local-vs-cloud latency percentiles, the $100/mo cap in practice; (3) surface p50/p95 latency per task_type.
**Demonstrates:** production LLMOps, cost-consciousness, observability that answers real questions.
**Effort:** medium. **Leverage:** medium-high.

### E. A model-migration / provider-abstraction case study
**Build:** turn the *retired-model incident* (Claude `claude-sonnet-4-20250514` 404'd silently for ~3 weeks) into a case study: the `complete()` abstraction, how a model swap is one config line, and the dependency self-health-check that now surfaces such breakage (Session 2). Optionally add a third provider to prove the abstraction.
**Demonstrates:** model-agnostic design, resilience thinking, and honest incident writeups (which senior reviewers love).
**Effort:** low (writeup) to medium (3rd provider). **Leverage:** medium-high — a real incident is more credible than a clean-room demo.

### F. Agent safety surface + prompt-injection guardrails (frame as OWASP LLM06)
**Build:** (1) document the *authorize-before-action* gates you already have — the skills system's shadow-mode + human-gate before irreversible actions (email send is draft-only, calendar writes) is exactly the senior guardrails differentiator; write it up as a least-privilege / kill-switch / authorize-before-action surface; (2) close the one real hole — wrap external text (`{{ user_input }}`, forwarded email) in explicit "data, not instructions" delimiters, move output-format instructions after the user block; (3) a short red-team page showing an injection attempt that fails.
**Demonstrates:** **OWASP LLM06 "Excessive Agency"** — the research's named senior differentiator for guardrails (not just prompt-injection awareness). You largely *have* this; it's under-documented.
**Effort:** low-medium (mostly documenting existing gates). **Leverage:** medium-high.

### G. A "local-LLM on one GPU" infra writeup
**Build:** publish the VRAM-budgeting analysis (this session produced it): why q6_K doesn't fit a 24 GB 3090 (~28 GB needed), why q5_K_M is the real upgrade, KV-cache quantization, contention with immich-ml. See §6.
**Demonstrates:** the systems/infra depth behind running local models — rare and differentiating.
**Effort:** low (analysis exists). **Leverage:** medium.

---

## 4. How to package it (the meta-move)

The code is strong; the *presentation* is where portfolio points are won or lost with this audience.

- **A "How this was built" section** (item #2 in the TL;DR): human sets direction + does adversarial review; AI implements; the Fable critique loop; this audit-and-remediate cycle. This is the single highest-ROI addition — it converts the biggest skeptic flag into evidence of a 2026-relevant skill.
- **Case-study pages** for 2–3 hard problems (the timezone bug, the retired-model incident, GPU budgeting). Senior reviewers read *how you think*, not feature counts.
- **Lead the README** with the AI-eng capabilities (§1), a short architecture diagram, and one screenshot each of: the eval dashboard, the cost dashboard, the skills-trust flow.
- **A 90-second demo** (capture → proactive nudge → schedule) so a reviewer sees it *work* in 90 seconds instead of reading 64k lines.
- **A one-line "wired vs defined" honesty note** — after this session you can credibly say "every headline feature is wired and tested," which most portfolio projects can't.

---

## 5. Market signals — what AI-Engineer hiring actually weights (2025-2026)

Synthesized from a dedicated web-research pass across five angles; the top claims were independently corroborated by 3+ searches. Sources at the end. *Caveat: many are practitioner/hiring-guide posts; the specific percentages trace mainly to the Datadog and O'Reilly reports and are directional. The qualitative consensus is strongly corroborated.*

**The three things that matter most (in order):**

1. **Evals as a discipline — the single highest-signal competency.** Every angle converged on this independently. The recurring framing: *"eval design is the single best signal of real LLM experience."* Anyone can demo five happy-path inputs; only production engineers can *measure* whether it works and detect when it stops. It's assessed by asking you to turn a vague spec into a measurable test suite — weak answers name a tool (RAGAS/DeepEval), strong ones start with "what does failure look like here." The **Datadog State of AI Engineering** gap is the headline: ~89% of teams instrument observability, only ~52% have systematic evals — and Donna currently sits on the wrong (observability-rich, eval-poor) side of exactly that line. This is why item A is #1.
2. **Failure-mode intuition + production survival.** The role reframed: not "can this person call an LLM API?" but "can they ship a reliable, observable, cost-controlled, safe system that survives six months of production traffic?" Failure-mode intuition is *earned, not read* — a 45-min production-incident walkthrough is now standard in senior loops. Donna's `dispatch_fallback_alert()` / no-silent-fallback discipline + the retired-model incident are strong material (items E, and the postmortem in §4).
3. **Cost discipline — "the most underrated skill in interviews, massively over-indexed once in the role."** Model per-conversation cost, route cheap-vs-frontier, cache, attribute tokens per trace. Candidates from lab/hobby contexts often *can't* discuss per-conversation pricing. Donna's Ollama+Claude routing + budget enforcement **is** this skill — but only if you surface the numbers (item D).

**Full competency ranking (frequency × weight):** (1) eval design; (2) observability/LLMOps — span-level tracing, not print-logging; (3) RAG + retrieval-quality measurement ("do hallucinations come from chunking, embedding, or reranker?" — recall@K/MRR, hybrid BM25+dense); (4) agentic system design — state/checkpointing, termination conditions, human-in-the-loop before irreversible actions; (5) cost/latency + model routing; (6) safety/guardrails — the senior differentiator is **OWASP LLM06 Excessive Agency**: least-privilege tool scopes, kill switches, *authorize-before-action* (not just prompt-injection awareness); (7) structured output — constrained generation, not try/except; (8) **context engineering** (the renamed "prompt engineering" — listing "prompt engineering" as a top skill is now a *red flag*); (9) **framework independence** — "what did you build without LangChain?"; (10) fine-tuning-vs-prompting judgment.

**Portfolio green flags (that map to Donna):** a published, scored eval harness with a golden dataset + regression tracking; per-invocation cost attribution on a dashboard; a model-routing **ADR** with measured tradeoffs; a documented failure-mode + real postmortem; a documented **agent safety surface** (least-privilege scopes, authorize-before-action on email/calendar writes, kill switch, injection tests — OWASP LLM06). **Red flags:** RAG/agent with no eval metrics ("never shipped"); "prompt engineering" or framework-only fluency foregrounded; no cost/latency numbers and no documented limitations.

**2025–2026 shifts to speak to fluently:** MCP won the standards war (know how to build/consume servers + their security surface — your "skills" system is a natural place to add or at least discuss MCP); *context engineering* replaced prompt engineering (context rot, 2–4× token growth/request); multi-model fleets are the norm (70%+ of orgs run 3+ models — frame your Ollama+Claude routing as fleet management with fallback chains); **eval-driven development is "the new tests-required."**

**Two Donna-specific advantages to name explicitly:** (a) it's built **without LangChain** — chunking, reranking, routing, state machine all first-principles, which directly answers the "framework independence" screen; (b) the skills system's shadow-mode + human-gate is already an **authorize-before-action** guardrail — the OWASP LLM06 senior differentiator — you just need to document it as such.

**Sources:** Evals — [hamel.dev](https://hamel.dev/blog/posts/evals/), [Eugene Yan](https://eugeneyan.com/writing/eval-process/), [Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/evals), [DeepEval EDD](https://deepeval.com/blog/eval-driven-development). Hiring bar — [TianPan](https://tianpan.co/blog/2026-04-16-hiring-llm-engineers-interview-what-to-test), [Digital Applied](https://www.digitalapplied.com/blog/ai-developer-hiring-skills-that-matter-2026), [KORE1](https://www.kore1.com/how-to-hire-llm-engineer-2026/). RAG eval — [zenvanriel](https://zenvanriel.com/ai-engineer-blog/rag-evaluation-metrics-that-matter/). Trends — [Datadog State of AI Engineering](https://www.datadoghq.com/state-of-ai-engineering/), [O'Reilly Agents Stack 2026](https://www.oreilly.com/radar/the-ai-agents-stack-2026-edition/), [LangChain State of Agent Engineering](https://www.langchain.com/state-of-agent-engineering).

---

## 6. Appendix — the q5/q6 GPU decision (worth publishing as item G)

Verdict from this session's VRAM analysis: **q6_K does not fit** the RTX 3090 (~26-27 GB weights + ~1 GB KV cache + driver overhead ≈ 28 GB vs 24 GB available; made worse by `immich-ml` intermittently taking 0.6-1.5 GB). Running it needs CPU-offloading ~6-8 layers, which halves throughput. **The real upgrade is q5_K_M** (~22-23 GB — fits with margin, a genuine quality step over the current q4_K_M). To try it:

```bash
docker exec donna-ollama ollama pull qwen2.5:32b-instruct-q5_K_M
# config/donna_models.yaml: local_parser.model -> qwen2.5:32b-instruct-q5_K_M
# config/llm_gateway.yaml:  gpu.home_model     -> qwen2.5:32b-instruct-q5_K_M  (keep the two in sync — there's now a drift-guard test)
# optional VRAM savings: OLLAMA_KV_CACHE_TYPE=q8_0 (flash attention already on)
# verify: nvidia-smi + `docker exec donna-ollama ollama ps` (PROCESSOR should read 100% GPU)
```

(We standardized on **q4_K_M** for now per your call; the config drift-guard test keeps the gateway/router tags aligned so the spurious per-call model swap can't come back.)
