import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Configs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("list view renders mocked configs", async ({ page }) => {
    await page.goto("/configs");
    await expect(page.getByRole("heading", { name: "Configs" })).toBeVisible();
    await expect(page.locator("text=task_states.yaml")).toBeVisible();
    await expect(page.locator("text=models.yaml")).toBeVisible();
  });

  test("no AntD Sider or Menu markup in Configs page", async ({ page }) => {
    await page.goto("/configs");
    await expect(page.locator(".ant-layout-sider")).toHaveCount(0);
    await expect(page.locator(".ant-menu")).toHaveCount(0);
  });

  test("navigates to editor subroute", async ({ page }) => {
    await page.goto("/configs");
    await page.click("text=task_states.yaml");
    await expect(page).toHaveURL(/\/configs\/task_states\.yaml/);
    await expect(page.locator("text=All files")).toBeVisible();
    await expect(page.getByRole("tab", { name: "Structured" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Raw YAML" })).toBeVisible();
  });

  test("empty state when no configs", async ({ page }) => {
    await page.route(/\/admin\/configs(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configs: [] }),
      }),
    );
    await page.goto("/configs");
    await expect(page.locator("text=No config files")).toBeVisible();
  });
});
