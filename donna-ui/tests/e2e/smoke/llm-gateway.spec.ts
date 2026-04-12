import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("LLM Gateway smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders page header and live status strip", async ({ page }) => {
    await page.goto("/llm-gateway");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "LLM Gateway" })).toBeVisible();

    // Range selector present
    const rangeTabs = page.locator('[role="tablist"] [role="tab"]');
    await expect(rangeTabs).toHaveCount(4);
  });

  test("sidebar nav item is active on /llm-gateway", async ({ page }) => {
    await page.goto("/llm-gateway");
    const link = page.getByRole("link", { name: "LLM Gateway" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("aria-current", "page");
  });

  test("no AntD class names on the page", async ({ page }) => {
    await page.goto("/llm-gateway");
    await page.waitForLoadState("networkidle");

    const antdCount = await page.locator('[class*="ant-"]').count();
    expect(antdCount).toBe(0);
  });
});
