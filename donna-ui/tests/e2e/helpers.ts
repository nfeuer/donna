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

    // Default: empty array for lists, empty object otherwise
    const body = url.match(
      /\/(logs|tasks|configs|prompts|shadow|preferences|rules|corrections)(\?|$)/,
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
