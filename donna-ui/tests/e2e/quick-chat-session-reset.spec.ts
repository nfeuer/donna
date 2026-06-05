import { test, expect } from "@playwright/test";
import { mockAdminApi } from "./helpers";

function mockChatApi(page: import("@playwright/test").Page) {
  return page.route("**/chat/**", (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.match(/\/chat\/sessions(\?|$)/) && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [] }),
      });
    }

    if (url.match(/\/chat\/sessions\/[^/]+\/messages(\?|$)/) && method === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          text: "Got it.",
          session_id: "session-logs",
          needs_escalation: false,
          suggested_actions: [],
        }),
      });
    }

    if (url.match(/\/chat\/sessions\/[^/]+$/) && method === "GET") {
      const sid = url.match(/\/chat\/sessions\/([^/?]+)/)![1];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session: {
            id: sid,
            user_id: "nick",
            channel: "dashboard_quick",
            status: "active",
            pinned_task_id: null,
            summary: null,
            created_at: "2026-05-17T00:00:00Z",
            last_activity: "2026-05-17T00:00:00Z",
            message_count: 2,
          },
          messages: [
            { id: "m1", role: "user", content: "hello from logs", created_at: "2026-05-17T00:00:00Z" },
            { id: "m2", role: "assistant", content: "Got it.", created_at: "2026-05-17T00:00:01Z" },
          ],
        }),
      });
    }

    if (url.match(/\/chat\/sessions\/[^/]+\/context-status(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          used_tokens: 100,
          max_tokens: 8192,
          compact_threshold: 6963,
          model_alias: "chat_respond",
        }),
      });
    }

    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

function mockChatApiWithSessions(page: import("@playwright/test").Page) {
  return page.route("**/chat/**", (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.match(/\/chat\/sessions(\?|$)/) && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          sessions: [
            {
              id: "chat-1",
              user_id: "nick",
              channel: "api",
              status: "active",
              pinned_task_id: null,
              summary: null,
              created_at: "2026-05-17T01:00:00Z",
              last_activity: "2026-05-17T01:10:00Z",
              message_count: 5,
            },
            {
              id: "chat-2",
              user_id: "nick",
              channel: "api",
              status: "closed",
              pinned_task_id: null,
              summary: null,
              created_at: "2026-05-16T10:00:00Z",
              last_activity: "2026-05-16T10:30:00Z",
              message_count: 3,
            },
            {
              id: "quick-1",
              user_id: "nick",
              channel: "dashboard_quick",
              status: "active",
              pinned_task_id: null,
              summary: null,
              created_at: "2026-05-17T02:00:00Z",
              last_activity: "2026-05-17T02:05:00Z",
              message_count: 2,
            },
          ],
        }),
      });
    }

    if (url.match(/\/chat\/sessions\/[^/]+$/) && method === "GET") {
      const sid = url.match(/\/chat\/sessions\/([^/?]+)/)![1];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session: {
            id: sid,
            user_id: "nick",
            channel: sid.startsWith("quick") ? "dashboard_quick" : "api",
            status: "active",
            pinned_task_id: null,
            summary: null,
            created_at: "2026-05-17T00:00:00Z",
            last_activity: "2026-05-17T00:00:00Z",
            message_count: 2,
          },
          messages: [
            { id: "m1", role: "user", content: "test message", created_at: "2026-05-17T00:00:00Z" },
            { id: "m2", role: "assistant", content: "response", created_at: "2026-05-17T00:00:01Z" },
          ],
        }),
      });
    }

    if (url.match(/\/chat\/sessions\/[^/]+\/context-status(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          used_tokens: 100,
          max_tokens: 8192,
          compact_threshold: 6963,
          model_alias: "chat_respond",
        }),
      });
    }

    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

// ---------------------------------------------------------------------------
// Chat page — session categories
// ---------------------------------------------------------------------------

test.describe("Chat page session categories", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await mockChatApiWithSessions(page);
  });

  test("shows Chat and Quick Chat categories with correct session counts", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForLoadState("networkidle");

    const chatHeader = page.locator("[class*=sessionGroupHeader]").filter({ hasNotText: "Quick" });
    const quickHeader = page.locator("[class*=sessionGroupHeader]").filter({ hasText: "Quick Chat" });

    await expect(chatHeader).toBeVisible();
    await expect(quickHeader).toBeVisible();

    await expect(chatHeader.locator("..")).toContainText("2");
    await expect(quickHeader.locator("..")).toContainText("1");
  });

  test("Chat section is expanded by default, Quick Chat is collapsed", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("text=5 msgs")).toBeVisible();
    await expect(page.locator("text=3 msgs")).toBeVisible();
    await expect(page.locator("text=2 msgs")).not.toBeVisible();
  });

  test("clicking Quick Chat header expands it and shows its sessions", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("text=2 msgs")).not.toBeVisible();
    await page.locator("[class*=sessionGroupHeader]").filter({ hasText: "Quick Chat" }).click();
    await expect(page.locator("text=2 msgs")).toBeVisible();
  });

  test("clicking Chat header collapses it", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("text=5 msgs")).toBeVisible();
    await page.locator("[class*=sessionGroupHeader]").filter({ hasNotText: "Quick" }).click();
    await expect(page.locator("text=5 msgs")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Quick Chat — session persistence per page
// ---------------------------------------------------------------------------

test.describe("Quick Chat session persistence", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await mockChatApi(page);
  });

  test("navigating away and back restores the previous session", async ({ page }) => {
    await page.goto("/logs");
    await page.waitForLoadState("networkidle");

    // Open quick chat and send a message to create a session
    await page.keyboard.press("Meta+j");
    await expect(page.locator("text=Quick Chat")).toBeVisible();

    const input = page.getByPlaceholder("Message Donna...");
    await input.fill("hello from logs");
    await input.press("Enter");

    await expect(page.locator("text=hello from logs")).toBeVisible();
    await expect(page.locator("text=Got it.")).toBeVisible();

    // Close panel, navigate away
    await page.locator("[class*=overlay]").click();
    await page.click('a[href="/tasks"]');
    await page.waitForURL("**/tasks");

    // Navigate back to logs
    await page.click('a[href="/logs"]');
    await page.waitForURL("**/logs");

    // Reopen quick chat — should restore the previous session
    await page.keyboard.press("Meta+j");
    await expect(page.locator("text=Quick Chat")).toBeVisible();
    await expect(page.locator("text=hello from logs")).toBeVisible();
    await expect(page.locator("text=Got it.")).toBeVisible();
  });

  test("new session button clears the conversation", async ({ page }) => {
    await page.goto("/logs");
    await page.waitForLoadState("networkidle");

    // Open quick chat and send a message
    await page.keyboard.press("Meta+j");
    const input = page.getByPlaceholder("Message Donna...");
    await input.fill("hello from logs");
    await input.press("Enter");

    await expect(page.locator("text=hello from logs")).toBeVisible();
    await expect(page.locator("text=Got it.")).toBeVisible();

    // Click the new session button
    await page.getByLabel("New session").click();

    // Messages should be gone, empty state shown
    await expect(page.locator("text=hello from logs")).not.toBeVisible();
    await expect(page.locator("text=Got it.")).not.toBeVisible();
    await expect(page.locator("text=Ask Donna anything about this page")).toBeVisible();
  });

  test("different pages have independent sessions", async ({ page }) => {
    await page.goto("/logs");
    await page.waitForLoadState("networkidle");

    // Send a message on logs page
    await page.keyboard.press("Meta+j");
    const input = page.getByPlaceholder("Message Donna...");
    await input.fill("hello from logs");
    await input.press("Enter");
    await expect(page.locator("text=Got it.")).toBeVisible();

    // Close and navigate to tasks
    await page.locator("[class*=overlay]").click();
    await page.click('a[href="/tasks"]');
    await page.waitForURL("**/tasks");

    // Open quick chat on tasks — should be empty (no session for this page)
    await page.keyboard.press("Meta+j");
    await expect(page.locator("text=Quick Chat")).toBeVisible();
    await expect(page.locator("text=Ask Donna anything about this page")).toBeVisible();
    await expect(page.locator("text=hello from logs")).not.toBeVisible();
  });

  test("new session after navigating back starts fresh", async ({ page }) => {
    await page.goto("/logs");
    await page.waitForLoadState("networkidle");

    // Create a session on logs
    await page.keyboard.press("Meta+j");
    const input = page.getByPlaceholder("Message Donna...");
    await input.fill("hello from logs");
    await input.press("Enter");
    await expect(page.locator("text=Got it.")).toBeVisible();

    // Navigate away and back
    await page.locator("[class*=overlay]").click();
    await page.click('a[href="/tasks"]');
    await page.waitForURL("**/tasks");
    await page.click('a[href="/logs"]');
    await page.waitForURL("**/logs");

    // Reopen — session is restored
    await page.keyboard.press("Meta+j");
    await expect(page.locator("text=hello from logs")).toBeVisible();

    // Click new session — starts fresh
    await page.getByLabel("New session").click();
    await expect(page.locator("text=Ask Donna anything about this page")).toBeVisible();
  });
});
