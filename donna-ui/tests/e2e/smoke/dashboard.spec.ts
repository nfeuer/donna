import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Dashboard smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders PageHeader, Segmented, and five cards", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // PageHeader with the "Dashboard" title.
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    // Segmented range selector has all four options.
    const rangeTabs = page.locator('[role="tablist"] [role="tab"]');
    await expect(rangeTabs).toHaveCount(4);
    await expect(rangeTabs.nth(0)).toHaveText("7d");
    await expect(rangeTabs.nth(2)).toHaveText("30d");

    // The "30d" option is selected by default.
    await expect(rangeTabs.nth(2)).toHaveAttribute("aria-selected", "true");

    // The grid has exactly 5 direct children — one fullWidth Cost
    // wrapper plus four cards. The grid is the last <div> child of
    // [data-testid="dashboard-root"] (PageHeader renders a <header>).
    const gridChildren = page.locator(
      '[data-testid="dashboard-root"] > div:last-child > *',
    );
    await expect(gridChildren).toHaveCount(5);
  });

  test("changing range triggers a re-fetch", async ({ page }) => {
    const seen: string[] = [];
    await page.route("**/admin/dashboard/**", (route) => {
      seen.push(route.request().url());
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "{}",
      });
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const initialCount = seen.length;
    expect(initialCount).toBeGreaterThan(0);

    // Click the "7d" option.
    await page.locator('[role="tablist"] [role="tab"]').nth(0).click();
    await page.waitForLoadState("networkidle");

    // At least one new request should have been issued with days=7.
    expect(seen.length).toBeGreaterThan(initialCount);
    expect(seen.some((u) => u.includes("days=7"))).toBeTruthy();
  });

  test("data-entered transitions to true after mount", async ({ page }) => {
    await page.goto("/");
    // The attribute flips inside a requestAnimationFrame — wait for it.
    const root = page.locator('[data-testid="dashboard-root"]');
    await expect(root).toHaveAttribute("data-entered", "true", { timeout: 2000 });
  });

  test("no AntD class names on the entire page", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const antdCount = await page.locator('[class*="ant-"]').count();
    expect(antdCount).toBe(0);
  });

  test("theme shortcut toggles data-theme attribute", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");
  });

  test("theme persists across page reload", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Clean up for next test
    await page.keyboard.press("Meta+.");
  });
});
