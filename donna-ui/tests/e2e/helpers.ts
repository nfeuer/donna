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

    // /admin/configs (list) returns { configs: [...] }
    if (url.match(/\/admin\/configs(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          configs: [
            { file: "task_states.yaml", size: 512, modified: "2026-04-01T12:00:00Z" },
            { file: "models.yaml", size: 384, modified: "2026-04-01T12:00:00Z" },
          ],
        }),
      });
    }

    // /admin/configs/:file returns { file, content, modified }
    if (url.match(/\/admin\/configs\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          file: "task_states.yaml",
          content:
            "states:\n  - name: backlog\n    color: muted\n  - name: done\n    color: success\n",
          modified: "2026-04-01T12:00:00Z",
        }),
      });
    }

    // /admin/prompts (list) returns { prompts: [...] }
    if (url.match(/\/admin\/prompts(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prompts: [
            { file: "intake.md", size: 256, modified: "2026-04-01T12:00:00Z" },
          ],
        }),
      });
    }

    // /admin/prompts/:file returns { file, content, variables, modified }
    if (url.match(/\/admin\/prompts\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          file: "intake.md",
          content:
            "# Intake Prompt\n\nHello {{ name }}, today is {{ date }}.\n\n```python\nprint('hi')\n```\n",
          variables: ["name", "date"],
          modified: "2026-04-01T12:00:00Z",
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
}
