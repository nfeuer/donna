import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Logs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/logs");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
