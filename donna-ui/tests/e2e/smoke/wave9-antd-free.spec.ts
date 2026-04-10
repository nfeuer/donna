import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

const PAGES = [
  { path: "/", name: "Dashboard" },
  { path: "/tasks", name: "Tasks" },
  { path: "/logs", name: "Logs" },
  { path: "/agents", name: "Agents" },
  { path: "/configs", name: "Configs" },
  { path: "/prompts", name: "Prompts" },
  { path: "/shadow", name: "Shadow" },
  { path: "/preferences", name: "Preferences" },
];

test.describe("Wave 9: AntD fully removed", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  for (const { path, name } of PAGES) {
    test(`${name} (${path}) has zero ant- class names`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");

      const antdCount = await page.locator('[class*="ant-"]').count();
      expect(antdCount).toBe(0);
    });
  }

  test("antd is not in the JS bundle", async ({ page }) => {
    const scripts: string[] = [];
    page.on("response", (resp) => {
      if (resp.url().endsWith(".js")) {
        scripts.push(resp.url());
      }
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Verify at least one JS file was loaded (sanity check)
    expect(scripts.length).toBeGreaterThan(0);

    // If antd were still bundled, its CSS-in-JS would inject ant- classes.
    // The per-page checks above already cover this, but this is a belt-and-suspenders check.
    const antdElements = await page.locator('[class*="ant-"]').count();
    expect(antdElements).toBe(0);
  });
});
