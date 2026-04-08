import type { Page } from "@playwright/test";

/**
 * Mock all /admin/** requests so smoke tests don't depend on a running backend.
 * Returns minimal empty-array / empty-object responses.
 */
export async function mockAdminApi(page: Page) {
  await page.route("**/admin/**", (route) => {
    const url = route.request().url();
    // Return empty array for list endpoints, empty object otherwise
    const body = url.match(/\/(logs|tasks|agents|configs|prompts|shadow|preferences|rules|corrections)(\?|$)/)
      ? "[]"
      : "{}";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body,
    });
  });
}
