import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Shadow smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
