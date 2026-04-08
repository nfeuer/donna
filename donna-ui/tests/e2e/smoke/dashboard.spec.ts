import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Dashboard smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/");
    // App renders *something* — either a nav rail or a root div
    await expect(page.locator("#root")).not.toBeEmpty();
  });

  test("theme shortcut toggles data-theme attribute", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Initial — no attribute (gold is default)
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    // Press Cmd+.
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Press Cmd+. again
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
