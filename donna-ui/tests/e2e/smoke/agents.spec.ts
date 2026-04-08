import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Agents smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
