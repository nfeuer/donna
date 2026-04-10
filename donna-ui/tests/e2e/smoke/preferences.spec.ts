import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Preferences smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads page with header and sections", async ({ page }) => {
    await page.goto("/preferences");
    await expect(page.getByRole("heading", { name: "Preferences" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Learned Rules/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Corrections/ })).toBeVisible();
  });

  test("rules table renders rows", async ({ page }) => {
    await page.goto("/preferences");
    await expect(page.getByText("Morning deep work")).toBeVisible();
  });

  test("rule click opens drawer with corrections", async ({ page }) => {
    await page.goto("/preferences");
    const row = page.locator("tr").filter({ hasText: "Morning deep work" }).first();
    await row.click();
    await expect(page.getByText("Rule Details")).toBeVisible();
    await expect(page.getByText("Supporting Corrections")).toBeVisible();
  });

  test("corrections section renders with filters", async ({ page }) => {
    await page.goto("/preferences");
    await expect(page.getByLabel("Filter corrections by field")).toBeVisible();
  });

  test("empty state renders when zero rules", async ({ page }) => {
    await page.route("**/admin/preferences/rules*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ rules: [], total: 0, limit: 50, offset: 0 }),
      }),
    );
    await page.goto("/preferences");
    await expect(page.getByText("No rules learned yet.")).toBeVisible();
    await expect(page.getByText("Donna picks these up as you correct her.")).toBeVisible();
  });
});
