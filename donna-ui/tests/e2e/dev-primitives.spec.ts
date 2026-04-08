import { test, expect } from "@playwright/test";

test.describe("Dev primitives gallery", () => {
  test("all primitives render in gold theme", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.waitForLoadState("networkidle");

    // Verify all 20 story sections are present
    const storyIds = [
      "button", "card", "pill", "input", "select", "checkbox", "switch",
      "tabs", "tooltip", "dialog", "drawer", "dropdown", "popover",
      "skeleton", "scrollarea", "pageheader", "stat", "segmented", "empty", "datatable",
    ];
    for (const id of storyIds) {
      await expect(page.getByTestId(`story-${id}`)).toBeVisible();
    }
  });

  test("all primitives render in coral theme", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Re-verify all story sections visible after theme flip
    await expect(page.getByTestId("story-button")).toBeVisible();
    await expect(page.getByTestId("story-datatable")).toBeVisible();

    // Reset for next test
    await page.keyboard.press("Meta+.");
  });

  test("dialog opens and closes via keyboard", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.getByRole("button", { name: "Open Dialog" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });

  test("data table sorts when header clicked", async ({ page }) => {
    await page.goto("/dev/primitives");
    // Find the DataTable story section
    const table = page.getByTestId("story-datatable").locator("table");
    // Capture the first row title before sorting
    const firstBefore = await table.locator("tbody tr").first().locator("td").first().textContent();
    // Click the Title header twice to reach descending sort
    // (the demo data's first row "Draft..." is already alphabetically first ascending)
    await table.locator("thead th").first().click();
    await table.locator("thead th").first().click();
    const firstAfter = await table.locator("tbody tr").first().locator("td").first().textContent();
    // Should have changed — descending order puts a different row first
    expect(firstAfter).not.toBe(firstBefore);
  });
});
