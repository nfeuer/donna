import { useMemo } from "react";

interface Props {
  content: string;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderMarkdown(md: string): string {
  let html = escapeHtml(md);

  // Code blocks (triple backtick)
  html = html.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    '<pre style="background:#262626;padding:8px;border-radius:4px;overflow-x:auto"><code>$2</code></pre>',
  );

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code style="background:#333;padding:1px 4px;border-radius:3px">$1</code>');

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4 style="color:#fff;margin:12px 0 4px">$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3 style="color:#fff;margin:12px 0 4px">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 style="color:#fff;margin:14px 0 6px">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 style="color:#fff;margin:16px 0 8px">$1</h1>');

  // Bold and italic
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Unordered lists
  html = html.replace(/^- (.+)$/gm, '<li style="margin-left:16px">$1</li>');

  // Ordered lists
  html = html.replace(/^\d+\.\s(.+)$/gm, '<li style="margin-left:16px">$1</li>');

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr style="border-color:#333;margin:12px 0" />');

  // Template variables highlighted
  html = html.replace(
    /\{\{\s*(\w+)\s*\}\}/g,
    '<span style="background:#1d3557;padding:1px 6px;border-radius:3px;color:#a8dadc">{{ $1 }}</span>',
  );

  // Line breaks
  html = html.replace(/\n/g, "<br />");

  return html;
}

export default function MarkdownPreview({ content }: Props) {
  const html = useMemo(() => renderMarkdown(content), [content]);

  return (
    <div
      style={{
        padding: 16,
        background: "#1a1a1a",
        borderRadius: 6,
        overflow: "auto",
        height: "calc(100vh - 320px)",
        fontSize: 13,
        lineHeight: 1.6,
        color: "#d4d4d4",
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
