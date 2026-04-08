import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Logs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders page header, filter bar, sidebar, and pagination", async ({ page }) => {
    await page.goto("/logs");

    // PageHeader is the new primitive composition.
    await expect(page.getByRole("heading", { name: "Logs" })).toBeVisible();

    // Filter bar controls (audit item P1: aria-labels).
    await expect(page.getByLabel("Search logs")).toBeVisible();
    await expect(page.getByLabel("Log level filter")).toBeVisible();
    await expect(page.getByLabel("Start time")).toBeVisible();
    await expect(page.getByLabel("End time")).toBeVisible();

    // Sidebar label from the new CSS Grid shell.
    await expect(page.getByLabel("Event type filter")).toBeVisible();

    // Pagination nav.
    await expect(page.getByLabel("Logs pagination")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();
  });

  test("save preset dialog opens and closes", async ({ page }) => {
    await page.goto("/logs");

    await page.getByRole("button", { name: /save current filters/i }).click();
    await expect(page.getByRole("heading", { name: "Save filter preset" })).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("heading", { name: "Save filter preset" })).not.toBeVisible();
  });
});
