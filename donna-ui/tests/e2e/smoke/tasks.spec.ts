import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Tasks smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders PageHeader, filter bar, and empty state", async ({ page }) => {
    await page.goto("/tasks");

    // PageHeader is the new primitive composition.
    await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();

    // Filter controls with explicit aria-labels.
    await expect(page.getByLabel("Search tasks")).toBeVisible();
    await expect(page.getByLabel("Status filter")).toBeVisible();
    await expect(page.getByLabel("Domain filter")).toBeVisible();
    await expect(page.getByLabel("Priority filter")).toBeVisible();

    // Reset button — disabled when no filter is dirty.
    await expect(page.getByLabel("Reset all task filters")).toBeDisabled();

    // Pagination nav from the new page shell.
    await expect(page.getByLabel("Tasks pagination")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();

    // Mocked empty response → EmptyState rendered.
    await expect(page.getByText("Nothing captured yet.")).toBeVisible();
  });

  test("reset button enables when search changes and clears all filters", async ({ page }) => {
    await page.goto("/tasks");

    const reset = page.getByLabel("Reset all task filters");
    await expect(reset).toBeDisabled();

    const search = page.getByLabel("Search tasks");
    await search.fill("deadline");
    await expect(reset).toBeEnabled();

    await reset.click();
    await expect(search).toHaveValue("");
    await expect(reset).toBeDisabled();
  });

  test("deep-linking /tasks/:id opens the drawer", async ({ page }) => {
    // Mock the detail endpoint with a minimal task payload so the drawer
    // body renders instead of showing "not found".
    await page.route("**/admin/tasks/abc123", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "abc123",
          user_id: "u1",
          title: "Deep-linked task",
          description: null,
          domain: "work",
          priority: 2,
          status: "in_progress",
          estimated_duration: null,
          deadline: null,
          deadline_type: null,
          scheduled_start: null,
          actual_start: null,
          completed_at: null,
          parent_task: null,
          prep_work_flag: false,
          prep_work_instructions: null,
          agent_eligible: false,
          assigned_agent: null,
          agent_status: null,
          tags: null,
          notes: null,
          reschedule_count: 0,
          created_at: "2026-04-08T12:00:00",
          created_via: "test",
          nudge_count: 0,
          quality_score: null,
          donna_managed: false,
          recurrence: null,
          dependencies: null,
          estimated_cost: null,
          calendar_event_id: null,
          invocations: [],
          nudge_events: [],
          corrections: [],
          subtasks: [],
        }),
      }),
    );

    await page.goto("/tasks/abc123");

    // Drawer dialog is open — Radix renders role="dialog".
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Deep-linked task")).toBeVisible();
  });

  test("ESC closes the drawer and returns to /tasks", async ({ page }) => {
    await page.route("**/admin/tasks/abc123", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "abc123",
          user_id: "u1",
          title: "Deep-linked task",
          description: null,
          domain: null,
          priority: 3,
          status: "backlog",
          estimated_duration: null,
          deadline: null,
          deadline_type: null,
          scheduled_start: null,
          actual_start: null,
          completed_at: null,
          parent_task: null,
          prep_work_flag: false,
          prep_work_instructions: null,
          agent_eligible: false,
          assigned_agent: null,
          agent_status: null,
          tags: null,
          notes: null,
          reschedule_count: 0,
          created_at: "2026-04-08T12:00:00",
          created_via: "test",
          nudge_count: 0,
          quality_score: null,
          donna_managed: false,
          recurrence: null,
          dependencies: null,
          estimated_cost: null,
          calendar_event_id: null,
          invocations: [],
          nudge_events: [],
          corrections: [],
          subtasks: [],
        }),
      }),
    );

    await page.goto("/tasks/abc123");
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page).toHaveURL(/\/tasks$/);
  });

  test("no AntD class names inside tasks-root", async ({ page }) => {
    await page.goto("/tasks");

    // Scope check to the Tasks shell, not PageHeader actions —
    // RefreshButton is still AntD until Wave 9. It lives in the
    // PageHeader `actions` slot, which is outside `[data-testid="tasks-root"] > div:not(header)`.
    const antdCount = await page
      .locator('[data-testid="tasks-root"] > *:not(header) [class*="ant-"]')
      .count();
    expect(antdCount).toBe(0);
  });
});
