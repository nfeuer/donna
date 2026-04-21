# Donna — AI Personal Assistant

Donna is an AI-powered personal assistant that **actively** manages tasks,
schedules, reminders, and delegates work to autonomous sub-agents. Named
after Donna Paulsen from *Suits* — sharp, confident, efficient, and always
one step ahead.

## The Problem

You forget to capture tasks, rarely check task lists, and don't schedule
time to do work. Donna fixes that by being proactive: pursuing you with
reminders, rescheduling dynamically, preparing for upcoming work, and
eventually doing the work for you.

## How to Use This Site

| If you want to… | Go to |
|---|---|
| Get it running locally | [Start Here → Install](start-here/install.md) |
| Understand the big picture | [Architecture → Overview](architecture/overview.md) |
| Walk through a concrete flow | [Workflows](workflows/index.md) |
| Read per-subsystem specs | [Domain](domain/index.md) |
| Jump into the code | [API Reference](reference/) |
| Inspect runtime configuration | [Config](config/) |
| See the authoritative spec | [Canonical Specs → spec_v3.md](reference-specs/spec-v3.md) |

## Canonical Reference

The authoritative design document is
[`spec_v3.md`](reference-specs/spec-v3.md) (v3.0, March 2026). All
architectural decisions trace back to it, and pages across this site cite
specific `§` sections where relevant.

## Stack at a Glance

- **Cloud LLM:** Claude API (Sonnet) — reasoning, parsing, agent work
- **Local LLM:** Ollama on RTX 3090 — classification and routing
- **Database:** SQLite (WAL) on NVMe + Supabase Postgres replica
- **Interaction:** Discord (primary), Twilio SMS/voice, Gmail
- **Observability:** Grafana + Loki, self-hosted

## Build Phases

| Phase | Goal | Status |
|-------|------|--------|
| **1: Foundation** | Task capture, scheduling, reminders, observability | Shipped |
| **2: Intelligence** | Multi-channel, prep work, corrections, priority escalation | Shipped |
| **3: Agents & Local LLM** | Sub-agents, local model, preference learning | Shipped |
| **4: UI & Multi-User** | Flutter app, second user, Firebase | API shipped; UI in sibling repo |
| **5: Automation** | Cron dispatch, cadence policy, lifecycle | Shipped |
