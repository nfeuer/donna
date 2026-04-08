import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Tasks smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
