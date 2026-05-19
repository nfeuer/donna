# Admin API Endpoints

All endpoints are under the `/admin` prefix. No authentication required today — this is a local dev tool bound to the loopback interface.

### Dashboard KPIs
| Endpoint | Description |
|----------|-------------|
| `GET /admin/dashboard/parse-accuracy?days=30` | Parse accuracy over time from correction_log vs invocation_log |
| `GET /admin/dashboard/agent-performance?days=30` | Per-agent call counts, latency, cost, success rates |
| `GET /admin/dashboard/task-throughput?days=30` | Tasks created vs completed, status distribution, overdue count |
| `GET /admin/dashboard/cost-analytics?days=30` | Daily/monthly spend, by task_type, by model, projections |

### Log Viewer
| Endpoint | Description |
|----------|-------------|
| `GET /admin/logs?event_type=&level=&service=&search=&start=&end=&limit=50&offset=0` | Paginated log query via Loki with fallback to invocation_log |
| `GET /admin/logs/trace/{correlation_id}` | All events for a correlation ID (trace timeline) |
| `GET /admin/logs/event-types` | Static event type hierarchy for tree filter |

### Invocations
| Endpoint | Description |
|----------|-------------|
| `GET /admin/invocations?task_type=&model=&is_shadow=&limit=50&offset=0` | Paginated invocation log |
| `GET /admin/invocations/{id}` | Single invocation with full output JSON |

### Tasks (Admin)
| Endpoint | Description |
|----------|-------------|
| `GET /admin/tasks?status=&domain=&priority=&search=&limit=50&offset=0` | Extended task list with agent/nudge/quality fields |
| `GET /admin/tasks/{id}` | Full task detail + linked invocations, nudges, corrections, subtasks |

### Configs & Prompts
| Endpoint | Description |
|----------|-------------|
| `GET /admin/configs` | List YAML config files with metadata |
| `GET /admin/configs/{filename}` | Read config file content |
| `PUT /admin/configs/{filename}` | Write config file (validates YAML, atomic write) |
| `GET /admin/prompts` | List prompt template files |
| `GET /admin/prompts/{filename}` | Read prompt file content |
| `PUT /admin/prompts/{filename}` | Write prompt file (atomic write) |

### Agents
| Endpoint | Description |
|----------|-------------|
| `GET /admin/agents` | List all agents with config + summary metrics from invocation_log |
| `GET /admin/agents/{name}` | Detailed agent view: config, recent invocations, daily latency, tool usage, cost summary |

### Shadow Scoring
| Endpoint | Description |
|----------|-------------|
| `GET /admin/shadow/comparisons?task_type=&days=30&limit=50` | Pair primary and shadow invocations by input_hash or task_id proximity |
| `GET /admin/shadow/stats?days=30` | Aggregate shadow vs primary quality and cost stats |
| `GET /admin/shadow/spot-checks?limit=50&offset=0` | Invocations flagged for review (spot_check_queued or quality < 0.7) |

### Preferences
| Endpoint | Description |
|----------|-------------|
| `GET /admin/preferences/rules?enabled=&rule_type=&limit=50` | List learned preference rules with filters |
| `PATCH /admin/preferences/rules/{id}` | Toggle rule enabled/disabled state |
| `GET /admin/preferences/corrections?field=&task_type=&limit=50&offset=0` | Paginated correction log |
| `GET /admin/preferences/stats` | Aggregate preference and correction statistics |

### Claude Inspector
| Endpoint | Description |
|----------|-------------|
| `GET /admin/claude/calls?task_type=&model=&date_from=&date_to=&min_cost=&min_tokens_in=&quality_score_below=&sort=&sort_dir=&limit=25&offset=0` | Paginated call browser with filters |
| `GET /admin/claude/calls/{invocation_id}/payload` | Full request/response JSON from disk |
| `GET /admin/claude/insights?days=7` | Computed waste-pattern insights (cost centers, prompt duplication, quality-cost mismatches, token bloat) |

### Escalations
| Endpoint | Description |
|----------|-------------|
| `GET /admin/escalations?status=&limit=50&offset=0` | List escalations with status filter |
| `GET /admin/escalations/{correlation_id}` | Escalation detail with full prompt, timeline, validation results |
| `POST /admin/escalations/{correlation_id}/submit` | Submit chat-mode answer for an open escalation |
| `POST /admin/escalations/{correlation_id}/validate` | Validate a submitted answer |

### Escalation Settings
| Endpoint | Description |
|----------|-------------|
| `GET /admin/escalation-settings` | All escalation settings with current values and slider cap |
| `PUT /admin/escalation-settings/{key:path}` | Update a single setting (optimistic locking via `expected_updated_at`) |
| `PUT /admin/escalation-settings/task-types/{task_type}` | Set per-task-type override (Auto / Force-API / Force-Manual / Disabled) |

### LLM Gateway
| Endpoint | Description |
|----------|-------------|
| `GET /admin/llm/analytics?days=7` | Per-caller analytics, queue stats, health data |
| `GET /admin/llm/queue/{item_id}/prompt` | Queue item prompt preview |

### Vault
| Endpoint | Description |
|----------|-------------|
| `GET /admin/vault/status` | Vault stats (note count, total size, last commit) |
| `GET /admin/vault/notes?folder=` | List notes with optional folder filter |
| `GET /admin/vault/notes/{path}` | Read a single note with frontmatter |
| `GET /admin/vault/history?limit=50` | Git commit history |

### Health
| Endpoint | Description |
|----------|-------------|
| `GET /admin/health` | Admin health check (DB, services, queue status) |
