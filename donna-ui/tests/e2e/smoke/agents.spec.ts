import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Agents smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders grid with agent cards", async ({ page }) => {
    await page.goto("/agents");
    // PageHeader renders
    await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
    // At least one agent card is rendered as a link
    const card = page.locator('a[href="/agents/test-agent"]');
    await expect(card).toBeVisible();
  });

  test("agent card has visible focus ring", async ({ page }) => {
    await page.goto("/agents");
    const card = page.locator('a[href="/agents/test-agent"]');
    await card.focus();
    // Focus ring is rendered via :focus-visible — just verify the element gets focus
    await expect(card).toBeFocused();
  });

  test("navigates to agent detail", async ({ page }) => {
    await page.goto("/agents");
    await page.click('a[href="/agents/test-agent"]');
    await expect(page).toHaveURL(/\/agents\/test-agent/);
    // Back link is present
    await expect(page.locator("text=All Agents")).toBeVisible();
    // Configuration section renders
    await expect(page.locator("text=Configuration")).toBeVisible();
  });

  test("detail page shows cost summary stats", async ({ page }) => {
    await page.goto("/agents/test-agent");
    await expect(page.locator("text=Cost Summary")).toBeVisible();
    await expect(page.locator("text=Total Invocations")).toBeVisible();
  });

  test("empty state when no agents", async ({ page }) => {
    // Override the agents mock to return empty
    await page.route("**/admin/agents", (route) => {
      if (route.request().url().match(/\/admin\/agents(\?|$)/)) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ agents: [] }),
        });
      }
      return route.continue();
    });
    await page.goto("/agents");
    await expect(page.locator("text=No agents configured")).toBeVisible();
  });
});
