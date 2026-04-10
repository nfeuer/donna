import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Shadow smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads page with header and charts", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByRole("heading", { name: "Shadow" })).toBeVisible();
    await expect(page.getByText("Quality Δ over time")).toBeVisible();
    await expect(page.getByText("Cost savings")).toBeVisible();
  });

  test("comparisons table renders rows", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByRole("heading", { name: /Comparisons/ })).toBeVisible();
    await expect(page.getByText("parse_task").first()).toBeVisible();
  });

  test("comparison row click opens drawer", async ({ page }) => {
    await page.goto("/shadow");
    const row = page.locator("tr").filter({ hasText: "parse_task" }).first();
    await row.click();
    await expect(page.getByText("Comparison Detail")).toBeVisible();
    await expect(page.getByText("Primary output", { exact: true })).toBeVisible();
    await expect(page.getByText("Shadow output", { exact: true })).toBeVisible();
  });

  test("spot checks section renders", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByText("Spot Checks")).toBeVisible();
  });

  test("keyboard nav on comparisons table", async ({ page }) => {
    await page.goto("/shadow");
    // Focus the first data row (tabIndex=0 when keyboardNav=true)
    const firstRow = page
      .locator("section")
      .filter({ hasText: /Comparisons/ })
      .locator("tbody tr")
      .first();
    await firstRow.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Comparison Detail")).toBeVisible();
  });
});
