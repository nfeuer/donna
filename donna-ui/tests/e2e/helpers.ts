import type { Page } from "@playwright/test";

/**
 * Mock all /admin/** requests so smoke tests don't depend on a running backend.
 * Returns minimal empty-array / empty-object responses.
 */
export async function mockAdminApi(page: Page) {
  await page.route("**/admin/**", (route) => {
    const url = route.request().url();

    // /admin/agents (list) returns { agents: [...] }
    if (url.match(/\/admin\/agents(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          agents: [
            {
              name: "test-agent",
              enabled: true,
              timeout_seconds: 30,
              autonomy: "medium",
              allowed_tools: ["web_search"],
              task_types: ["research"],
              total_calls: 42,
              avg_latency_ms: 350,
              total_cost_usd: 1.23,
              last_invocation: "2026-04-01T12:00:00Z",
            },
          ],
        }),
      });
    }

    // /admin/agents/:name (detail) returns full detail
    if (url.match(/\/admin\/agents\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "test-agent",
          enabled: true,
          timeout_seconds: 30,
          autonomy: "medium",
          allowed_tools: ["web_search"],
          task_types: ["research"],
          total_calls: 42,
          avg_latency_ms: 350,
          total_cost_usd: 1.23,
          last_invocation: "2026-04-01T12:00:00Z",
          recent_invocations: [],
          daily_latency: [],
          tool_usage: [],
          cost_summary: { total_calls: 42, total_cost_usd: 1.23, avg_cost_per_call: 0.0293 },
        }),
      });
    }

    // /admin/configs (list) returns { configs: ConfigFile[] } where
    // ConfigFile = { name, size_bytes, modified (epoch seconds) }
    if (url.match(/\/admin\/configs(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          configs: [
            { name: "task_states.yaml", size_bytes: 512, modified: 1774972800 },
            { name: "models.yaml", size_bytes: 384, modified: 1774972800 },
          ],
        }),
      });
    }

    // /admin/configs/:name returns ConfigContent = { name, content, size_bytes, modified }
    if (url.match(/\/admin\/configs\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "task_states.yaml",
          content:
            "states:\n  - name: backlog\n    color: muted\n  - name: done\n    color: success\n",
          size_bytes: 512,
          modified: 1774972800,
        }),
      });
    }

    // /admin/prompts (list) returns { prompts: PromptFile[] } where
    // PromptFile = { name, size_bytes, modified (epoch seconds) }
    if (url.match(/\/admin\/prompts(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prompts: [
            { name: "intake.md", size_bytes: 256, modified: 1774972800 },
          ],
        }),
      });
    }

    // /admin/prompts/:name returns PromptContent = { name, content, size_bytes, modified }
    if (url.match(/\/admin\/prompts\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "intake.md",
          content:
            "# Intake Prompt\n\nHello {{ name }}, today is {{ date }}.\n\n```python\nprint('hi')\n```\n",
          size_bytes: 256,
          modified: 1774972800,
        }),
      });
    }

    // /admin/shadow/comparisons
    if (url.match(/\/admin\/shadow\/comparisons(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          comparisons: [
            {
              primary: {
                id: "p1",
                timestamp: "2026-04-01T12:00:00Z",
                task_type: "parse_task",
                task_id: "t1",
                model_alias: "claude-sonnet",
                model_actual: "claude-sonnet-4-20250514",
                input_hash: "abc",
                latency_ms: 450,
                tokens_in: 200,
                tokens_out: 100,
                cost_usd: 0.0025,
                output: { title: "Primary output" },
                quality_score: 0.82,
                is_shadow: false,
                spot_check_queued: false,
                user_id: "u1",
              },
              shadow: {
                id: "s1",
                timestamp: "2026-04-01T12:00:00Z",
                task_type: "parse_task",
                task_id: "t1",
                model_alias: "qwen-32b",
                model_actual: "qwen2.5:32b-instruct-q6_K",
                input_hash: "abc",
                latency_ms: 1200,
                tokens_in: 200,
                tokens_out: 110,
                cost_usd: 0.0,
                output: { title: "Shadow output" },
                quality_score: 0.91,
                is_shadow: true,
                spot_check_queued: false,
                user_id: "u1",
              },
              quality_delta: 0.09,
            },
          ],
          total: 1,
        }),
      });
    }

    // /admin/shadow/stats
    if (url.match(/\/admin\/shadow\/stats(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          primary_avg_quality: 0.82,
          shadow_avg_quality: 0.91,
          avg_delta: 0.09,
          wins: 34,
          losses: 12,
          ties: 6,
          primary_cost: 5.2,
          shadow_cost: 1.0,
          primary_count: 52,
          shadow_count: 52,
          trend: [
            { date: "2026-03-25", avg_quality: 0.85, count: 8 },
            { date: "2026-03-26", avg_quality: 0.88, count: 10 },
            { date: "2026-03-27", avg_quality: 0.91, count: 7 },
          ],
          days: 30,
        }),
      });
    }

    // /admin/shadow/spot-checks
    if (url.match(/\/admin\/shadow\/spot-checks(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "sc1",
              timestamp: "2026-04-01T10:00:00Z",
              task_type: "parse_task",
              task_id: "t2",
              model_alias: "claude-sonnet",
              model_actual: "claude-sonnet-4-20250514",
              latency_ms: 300,
              tokens_in: 150,
              tokens_out: 80,
              cost_usd: 0.0018,
              quality_score: 0.88,
              is_shadow: false,
              spot_check_queued: true,
              user_id: "u1",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/rules
    if (url.match(/\/admin\/preferences\/rules(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          rules: [
            {
              id: "r1",
              user_id: "u1",
              rule_type: "scheduling",
              rule_text: "Morning deep work blocks before 11am",
              confidence: 0.91,
              condition: { time_before: "11:00" },
              action: { block_type: "deep_work" },
              supporting_corrections: ["c1", "c2"],
              enabled: true,
              created_at: "2026-03-15T08:00:00Z",
              disabled_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/rules/:id (PATCH toggle)
    if (url.match(/\/admin\/preferences\/rules\/[^/?]+/) && route.request().method() === "PATCH") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "r1",
          user_id: "u1",
          rule_type: "scheduling",
          rule_text: "Morning deep work blocks before 11am",
          confidence: 0.91,
          condition: { time_before: "11:00" },
          action: { block_type: "deep_work" },
          supporting_corrections: ["c1", "c2"],
          enabled: true,
          created_at: "2026-03-15T08:00:00Z",
          disabled_at: null,
        }),
      });
    }

    // /admin/preferences/corrections (with optional ?rule_id=)
    if (url.match(/\/admin\/preferences\/corrections(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          corrections: [
            {
              id: "c1",
              timestamp: "2026-04-01T09:00:00Z",
              user_id: "u1",
              task_type: "parse_task",
              task_id: "t1",
              input_text: "Schedule standup for tomorrow",
              field_corrected: "priority",
              original_value: "low",
              corrected_value: "high",
              rule_extracted: "r1",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/stats
    if (url.match(/\/admin\/preferences\/stats(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_rules: 14,
          active_rules: 11,
          disabled_rules: 3,
          avg_confidence: 0.82,
          total_corrections: 87,
          top_fields: [
            { field: "priority", count: 34 },
            { field: "deadline", count: 22 },
            { field: "domain", count: 18 },
          ],
        }),
      });
    }

    // Default: empty array for lists, empty object otherwise
    const body = url.match(
      /\/(logs|tasks|shadow|preferences|rules|corrections)(\?|$)/,
    )
      ? "[]"
      : "{}";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body,
    });
  });

  // Mock /llm/queue/** endpoints for the dashboard LLM queue card
  await page.route("**/llm/queue/**", (route) => {
    const url = route.request().url();

    if (url.match(/\/llm\/queue\/status/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          current_request: null,
          internal_queue: { pending: 0, next_items: [] },
          external_queue: { pending: 0, next_items: [] },
          stats_24h: {
            internal_completed: 12,
            external_completed: 5,
            external_interrupted: 1,
          },
          rate_limits: {},
          mode: "active",
        }),
      });
    }

    if (url.match(/\/llm\/queue\/stream/)) {
      // SSE: return a simple initial event then hang
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'data: {"current_request":null,"internal_queue":{"pending":0,"next_items":[]},"external_queue":{"pending":0,"next_items":[]},"stats_24h":{"internal_completed":12,"external_completed":5,"external_interrupted":1},"rate_limits":{},"mode":"active"}\n\n',
      });
    }

    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "{}",
    });
  });
}
