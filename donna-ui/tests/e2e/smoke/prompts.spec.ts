import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Prompts smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("list view renders mocked prompts", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.getByRole("heading", { name: "Prompts" })).toBeVisible();
    await expect(page.getByRole("link", { name: /intake/ })).toBeVisible();
  });

  test("no AntD Sider or Menu markup in Prompts page", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.locator(".ant-layout-sider")).toHaveCount(0);
    await expect(page.locator(".ant-menu")).toHaveCount(0);
  });

  test("navigates to editor with Edit/Preview/Split tabs", async ({ page }) => {
    await page.goto("/prompts");
    await page.getByRole("link", { name: /intake/ }).first().click();
    await expect(page).toHaveURL(/\/prompts\/intake\.md/);
    await expect(page.getByRole("tab", { name: "Edit" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Preview" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Split" })).toBeVisible();
  });

  test("variable inspector shows template variables", async ({ page }) => {
    await page.goto("/prompts/intake.md");
    await expect(page.getByText("Template variables")).toBeVisible();
    // The `.variable` pills (inside preview) and the VariableInspector's own
    // muted pills both render the full `{{ name }}` literal.
    await expect(page.getByText("{{ name }}").first()).toBeVisible();
    await expect(page.getByText("{{ date }}").first()).toBeVisible();
  });

  test("preview renders code block with syntax highlighting", async ({ page }) => {
    await page.goto("/prompts/intake.md");
    await page.getByRole("tab", { name: "Preview" }).click();
    // rehype-highlight tags the fenced block `<code class="language-python">`
    // and wraps tokens in `<span class="hljs-*">`. Task 15's sanitize schema
    // (c9bc5e5) whitelists both. Assert the presence of a highlighted token
    // span inside the language-python code block to prove both the language
    // class and the hljs-* classes survived sanitize.
    await expect(page.locator("code.language-python")).toBeVisible();
    await expect(
      page.locator("code.language-python span[class^='hljs-']").first(),
    ).toBeVisible();
  });

  // ------------------------------------------------------------------
  // XSS regression — proves old MarkdownPreview vector is closed.
  // ------------------------------------------------------------------
  test("XSS: script/img/iframe/javascript: payloads do not execute", async ({ page }) => {
    // Override ONLY the detail endpoint; list stays mocked so navigation works.
    await page.route(/\/admin\/prompts\/[^/?]+/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "xss.md",
          content:
            "# XSS Test\n\n" +
            "<script>window.__xss_script = true;</script>\n\n" +
            '<img src="x" onerror="window.__xss_img = true">\n\n' +
            "[click me](javascript:window.__xss_href=true)\n\n" +
            '<iframe src="javascript:window.__xss_iframe=true"></iframe>\n',
          size_bytes: 256,
          modified: 1774972800,
        }),
      }),
    );

    await page.goto("/prompts/xss.md");
    await page.getByRole("tab", { name: "Preview" }).click();

    // Give react-markdown time to render.
    await page.waitForTimeout(200);

    // Trial-click the sanitized link if it still exists (it shouldn't have an
    // executable href after sanitize).
    const link = page.locator("a", { hasText: "click me" });
    if (await link.count()) await link.click({ trial: true }).catch(() => {});

    // None of the payload flags should be set anywhere in the window.
    const flags = await page.evaluate(() => ({
      script: (window as unknown as { __xss_script?: boolean }).__xss_script === true,
      img: (window as unknown as { __xss_img?: boolean }).__xss_img === true,
      href: (window as unknown as { __xss_href?: boolean }).__xss_href === true,
      iframe: (window as unknown as { __xss_iframe?: boolean }).__xss_iframe === true,
    }));
    expect(flags.script).toBe(false);
    expect(flags.img).toBe(false);
    expect(flags.href).toBe(false);
    expect(flags.iframe).toBe(false);

    // Structural assertions — sanitizer must strip these entirely from the
    // rendered preview. Scope to the Preview tabpanel so Vite's dev-mode
    // <script type="module"> tags in <head>/<body> don't create false
    // positives. The only scripts outside this scope are the app's own
    // dev-server scripts — never content injected via the markdown payload.
    const previewPanel = page.getByRole("tabpanel", { name: "Preview" });
    await expect(previewPanel.locator("script")).toHaveCount(0);
    await expect(previewPanel.locator("img[onerror]")).toHaveCount(0);
    await expect(previewPanel.locator("iframe")).toHaveCount(0);
    await expect(previewPanel.locator('a[href^="javascript:"]')).toHaveCount(0);
  });

  test("XSS: template variable in attribute context is not evaluated", async ({ page }) => {
    await page.route(/\/admin\/prompts\/[^/?]+/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "attr.md",
          content:
            "Hello {{ name }}, visit [our site](http://example.com).\n\n" +
            "![alt]({{ url }})\n",
          size_bytes: 128,
          modified: 1774972800,
        }),
      }),
    );
    await page.goto("/prompts/attr.md");
    await page.getByRole("tab", { name: "Preview" }).click();
    // Variable placeholder must render as visible text, not as an attribute value.
    await expect(page.getByText("{{ name }}").first()).toBeVisible();
    // No images should point to a literal `{{` string — markdown parser will
    // not interpret it as a valid URL.
    const badImg = page.locator('img[src*="{{"]');
    expect(await badImg.count()).toBe(0);
  });
});
