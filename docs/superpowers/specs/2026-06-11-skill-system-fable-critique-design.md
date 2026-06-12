# Skill System — Fable Critique & Redesign Spec

**Date:** 2026-06-11
**Status:** Triaged — pending owner decision on scope + the auto-draft human-gate default (§6)
**Critic:** Fable 5 (adversarial design critique)
**Triage:** Opus (independent verification)
**Related:** `spec_v3.md §23` (Skills & Capabilities System), `CLAUDE.md` principles #1 (config), #2 (safety-first), #5 (model abstraction), #6 (tool validation), no-`contextlib.suppress` rule. Wave C of the Fable critique program.

> Fifth and final Fable run. Captures the **verified** findings, **disposition**, and
> the owner decisions. No code changed.

---

## 1. Executive finding (verified)

**The Skill System's gate *logic* is well-engineered, but the evidence pipeline the gates
depend on is unwired in production — so the trust machinery is decorative *in effect*, not
in code.** The production executor factory (`cli_wiring.py:2325`) builds `SkillExecutor`
with no `run_sink`, `run_repository`, or `shadow_sampler`; `SkillRunRepository` is never
constructed in production, and the `ShadowSampler` built at `startup_wiring.py:78` is never
handed to an executor. Every statistical trust gate — sandbox→shadow→trusted promotion,
and `degraded` auto-demotion — reads `skill_run`/divergence data that is **never produced**.
Promotion fails *safe* (skills stall in sandbox), but **monitoring fails dangerous**: seed
skills promoted to `shadow_primary` *by alembic migration* execute live with real tools and
**zero divergence recording**, so the §23.4 auto-demotion safety net is structurally inert.

**Good news (verified):** model-abstraction + cost discipline are genuinely held — auto-drafter,
evolver, executor, and shadow sampler all route through `complete()` with budget pre-checks and
config daily caps (`config/skills.yaml`). And auto-draft *containment* is the strongest seam in
the subsystem (drafts enter at the bottom, fixture-validated against a mock registry, any failure
dismisses). The flaw is the evidence plumbing and two latent landmines, not the gate design.

## 2. Verification log (Opus, independent)

| # | Claim | Verified? | Evidence |
|---|---|---|---|
| **1** | Evidence loop unwired in production | ✅ | `cli_wiring.py:2325` `SkillExecutor(model_router, config, tool_gap_surfacer)` — no run sink/repo/sampler. `SkillRunRepository(` has zero prod construction. `ShadowSampler` built `startup_wiring.py:78` but not handed to the executor. `alembic/versions/promote_seed_skills_to_shadow_primary.py` exists. |
| **2** | `requires_human_gate` blocks safety DEMOTIONS | ✅ | `lifecycle.py:208` `if requires_human_gate and actor == "system": raise HumanGateRequiredError` — fires on any system transition, including a degradation demotion, not just promotions. |
| **5** | `contextlib.suppress` dead-hop in evolution | ✅ | `evolution.py:271` `with contextlib.suppress(IllegalTransitionError):` around a draft→sandbox hop attempted with `reason="gate_passed"` that the table never allows → always-failing, knowingly dead (comments at :261/:270). Violates the no-suppress rule. |
| **3** | Gate evidence is skill-scoped not version-scoped | ✅ (Fable) | `lifecycle.py:307-311` counts `skill_run WHERE skill_id=?`; no `skill_version_id` filter — evolved versions inherit predecessor's track record. |
| **4** | Tool allowlist authored by the skill's own LLM-written YAML | ✅ (Fable) | `executor.py:202` `allowed_tools = step.get("tools", [])`; capability `tools:` grants in config consumed only for availability checks, never as authorization. |

**Conclusion:** the critique is accurate; the headline + both landmines + the suppress violation are confirmed.

## 3. Triage dispositions

Legend: **ACCEPT** · **ESCALATE** (owner) · **DEFER** (trigger-gated) · **KEEP**.

### The evidence-loop slice (couple these — #1 arms #2 and #3)
| # | Finding | Sev | Disposition |
|---|---|---|---|
| 1 | Wire `SkillRunRepository` + the already-built `ShadowSampler` into `_automation_skill_executor_factory`; add a boot invariant: refuse to start (or alert) if `skill_system.enabled` and any skill is in shadow_primary/trusted but the executor lacks run-persistence + sampler | S1 | **ESCALATE** — converts the trust machinery from decorative to functional, but **changes prod behavior** (adds live shadow-sampling LLM cost; enables auto-demotion). Owner call §6. |
| 2 | Scope the `requires_human_gate` check to **promotion** edges (don't veto `reason="degradation"` demotions); add per-skill try/except + alert in `DegradationDetector.run()` so one raise can't abort the whole nightly sweep | S1 | **ACCEPT (with #1)** — must land with #1 or activating the loop blocks the very demotions it should enable. |
| 3 | Version-scope the gate queries (`r.skill_version_id = skill.current_version_id`); reset `baseline_agreement` to NULL on version swap | S2→S1 once #1 lands | **ACCEPT (with #1)** — otherwise an evolved version inherits its predecessor's runs and clears gates with zero runs of its own code (re-vetting is vacuous). |

### Safety hygiene (low-risk, principle-compliance — do regardless)
| # | Finding | Sev | Disposition |
|---|---|---|---|
| 5 | Delete the `contextlib.suppress(IllegalTransitionError)` dead-hop + dead `to_state` logic in `evolution.py`; log/alert "evolved version parked in draft awaiting approval"; enforce `human_approval` semantics in `transition()` (require non-null `actor_id` + `actor=="user"`) | S2 | **ACCEPT** — clears a direct CLAUDE.md no-suppress violation + makes the draft→sandbox human gate code-enforced, not honor-system. |
| 7 | No `dispatch_fallback_alert` anywhere in `src/donna/skills/` — run-persistence failures, shadow-sample loss, and degradation demotions are all silent | S2 | **ACCEPT** — inject the notifier into `DegradationDetector` (already available in `assemble_skill_system`); alert on demotion + on persistence/shadow failure streaks. The user must be told when a trusted skill is demoted. |
| 10 | Spec/doc drift: §23.5 says a human promotes into *shadow*, but code gates only draft→sandbox (then fully automatic); doc lifecycle.md degradation wording (CI lower vs upper bound — code is the stricter, correct one); `maybe_sample` vs `sample_if_applicable` | S3 | **ACCEPT (doc)** — reconcile §23.5 to the real contract ("one human approval at draft→sandbox; statistical gates thereafter"). The auto-draft default is a **today-decision** (§6). |

### Trust-gate rigor (accept, lower urgency)
| # | Finding | Disposition |
|---|---|---|
| 6 | Sandbox validity gate counts only `status='succeeded'`, which a skill's own `on_failure: continue` can manufacture; fixture `expected_output_shape` not checked on sandbox runs; no failure-rate ceiling on shadow→trusted | **ACCEPT (with #1)** — count a sandbox run valid only if `final_output` validates against the capability's `default_output_shape` and no step `continued`/`failed`; add a failure-rate ceiling to shadow→trusted. |
| 9 | Evolution gates vacuous-pass on empty evidence (no cases/fixtures/runs → pass) | **ACCEPT (small)** — fail closed (or require a configurable minimum) when the evidence set is empty; alert on a vacuous pass. |
| 8 | Dormant ungated path: `orchestrator/dispatcher.py:244-266` runs a matched skill with no `skill.state` check (would run a DRAFT). Dead today (`skill_executor=` never wired) | **DEFER/doc** — copy `_decide_path`'s state check or delete the Phase-1 path when that routing is wired. Note in the dormant-pipeline doc (sibling to the §7.2 dormancy). |

### Tool authorization (#4)
| # | Finding | Disposition |
|---|---|---|
| 4 | The skills tool dispatch is real (not bypassable like the agent-layer one) but verifies the skill's tool list against *itself* — the allowlist is authored by the LLM that wrote the skill. Capability `tools:` config grants exist but aren't used as authorization | **DEFER (enforce) + ACCEPT (config now)** — full dispatch-time intersection (step tools ∩ capability's config grant, fail-closed) lands when the **first write-capable tool registers** (trigger; today the registry is read-mostly — `calendar_read`/`task_db_read`/`web_fetch` — so blast radius is capped). The config-side `tools:` declarations can be completed now at zero risk. |

### KEEP — right as-is; a naive critic would break these
- **The lifecycle state machine** (sole-mutator, explicit from/to/reason table, audit row per transition) — rigidity is the feature; evolution.py's failed sneak-past *proves the table works*.
- **Auto-draft containment** (enter at the bottom; mandatory fixture validation against a mock registry with an absorbing sink; failure dismisses) — the strongest seam; don't add "more human gates everywhere."
- **Model abstraction + cost discipline** — all skill LLM calls route through `complete()` with budget pre-checks + daily caps. No second accounting layer.
- **Evolution only on `degraded` skills, downstream of a human flagged→degraded approval** — correctly conservative; don't "proactively evolve" trusted skills.
- **Degradation's CI-upper-bound-below-baseline test** — stricter than the doc; fix the doc, not the code.

### CHALLENGES an open item
- **G-2** ("SkillSystemConfig fields not read; thresholds hardcoded") is **stale** — lifecycle, degradation, evolution-gates, and shadow all read injected config (`lifecycle.py:304-305,352-353`, `degradation.py:73`, `evolution_gates.py:159,206`). Re-scope or close G-2.

## 4. Highest-leverage change

**Wire the evidence loop and defuse the two landmines it arms, in one slice:** inject
`SkillRunRepository` + the bundle's `ShadowSampler` into the production executor factory,
scope the `requires_human_gate` check to promotion edges (#2), and version-scope the gate
queries (#3). This converts the entire §23.4 trust machinery from decorative to functional —
promotion gates get real data, shadow_primary/trusted skills get the monitoring the spec
promises, and auto-demotion becomes possible. Every other finding is secondary until the
gates have data.

## 5. Defer (trigger-gated)
- **Capability-grant tool authorization (#4) full enforcement** — trigger: first write-capable tool registration (`task_db_write`/`calendar_write`, §23.3 Stage 3). Config `tools:` declarations completable now.
- **OOS-8 auto-flag `requires_human_gate` from sensitive tools** — keep deferred; but decide the auto-draft *default* now (§6).
- **Continuous-score drift detection (F-W1-A)** — already deferred with a trigger; don't redesign degradation stats now.

## 5b. Owner addendum — surface pending draft approvals (required with the gate flip)

Flipping the auto-draft default to `requires_human_gate=1` (§6 decision) means
auto-drafted skills park in `draft` awaiting approval. That is only safe if the
user is **told**, or drafts pile up unseen (the silent anti-pattern). Required:

1. **Discord notification on a new draft awaiting approval.** When an auto-drafted
   skill is parked in `draft` with `requires_human_gate`, dispatch a user-facing
   `NotificationService` message ("New drafted skill '<name>' awaiting your
   approval — review at <dashboard/route>"). This is distinct from the internal
   `dispatch_fallback_alert` (#7): it is a normal user notification, not a
   degradation alert.
2. **Digest visibility.** Add a "Pending skill approvals" section to the digest
   (EOD and/or morning) listing skills in `draft` with `requires_human_gate=1`,
   so a missed Discord ping still surfaces. Query: skills in DRAFT state awaiting
   human approval; show name + age.

Implemented on top of the core slice (this session).

## 6. Owner decisions (escalated)
1. **Scope:** the evidence-loop slice (#1+#2+#3+#6, activates the trust machinery — adds live shadow-sample cost, enables auto-demotion) vs the safety-hygiene set (#5+#7+#10, principle-compliance, no prod-behavior change) vs both.
2. **Auto-draft human-gate default (#10a):** today, auto-drafted skills set `requires_human_gate=0` → one sandbox approval then fully automatic to trusted. Flip the default to `1` (require human approval before sandbox), per safety-first? A one-bit posture decision.

---

## Appendix — program mechanics (Wave C, final)

- Triaging Fable's attention to the gating seams (vs reading all ~6,700 LOC) surfaced the
  structural gap fast: the gate *code* is sound, so a line-by-line review would have praised
  it; the defect is that the *evidence it consumes is never produced* — a wiring-layer flaw
  only a "trace the data end-to-end" lens finds.
- Fable also **challenged an open item** (G-2 stale) and **confirmed the KEEPs** (auto-draft
  containment, model-abstraction discipline) — the PROVEN-vs-SUSPECTED + corroborate/challenge
  discipline paid off a fifth time.
