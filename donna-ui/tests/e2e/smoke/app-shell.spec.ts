import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("App shell", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("rail shows Donna wordmark and nav items", async ({ page }) => {
    await page.goto("/");
    // The inner <nav> carries aria-label="Primary navigation".
    const nav = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(nav).toBeVisible();
    // Brand wordmark lives on the aside, not inside the nav.
    await expect(page.getByText("Donna", { exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Tasks" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Preferences" })).toBeVisible();
  });

  test("active nav item reflects the current route", async ({ page }) => {
    await page.goto("/tasks");
    const tasksLink = page.getByRole("link", { name: "Tasks" });
    await expect(tasksLink).toHaveAttribute("aria-current", "page");

    const dashboardLink = page.getByRole("link", { name: "Dashboard" });
    await expect(dashboardLink).not.toHaveAttribute("aria-current", "page");
  });

  test("theme toggle chips flip the data-theme attribute", async ({ page }) => {
    await page.goto("/");
    // Initial: gold (no attribute)
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    await page.getByRole("button", { name: "Electric coral theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");
    await expect(page.getByRole("button", { name: "Electric coral theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await page.getByRole("button", { name: "Champagne gold theme" }).click();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");
  });

  test("pressing ? opens the keyboard shortcuts dialog, Esc closes it", async ({ page }) => {
    await page.goto("/");
    // Take focus off any input first
    await page.locator("body").click();

    await page.keyboard.press("?");
    const dialog = page.getByRole("dialog", { name: "Keyboard Shortcuts" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
  });

  test("no AntD Header element is present", async ({ page }) => {
    // The old Layout.tsx rendered an AntD .ant-layout-header. Wave 2 removes it.
    await page.goto("/");
    await expect(page.locator(".ant-layout-header")).toHaveCount(0);
  });
});
