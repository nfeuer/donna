import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Prompts smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
