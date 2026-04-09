import { memo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import styles from "./MarkdownPreview.module.css";

interface Props {
  content: string;
}

// Lock sanitization to defaultSchema. defaultSchema already disallows raw
// <script>, <iframe>, and on* handler attributes — we explicitly re-declare
// the choice here so a future diff that touches the schema is obvious.
const SANITIZE_SCHEMA = defaultSchema;

function MarkdownPreviewImpl({ content }: Props) {
  return (
    <div className={styles.root}>
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, SANITIZE_SCHEMA], rehypeHighlight]}
        components={{
          // Highlight template variables: render `{{ foo }}` as an inline pill.
          // Runs on text nodes only — the regex has no HTML capture, so it
          // cannot reintroduce the old injection surface.
          p: ({ children }) => <p>{highlightVariables(children)}</p>,
          li: ({ children }) => <li>{highlightVariables(children)}</li>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function highlightVariables(children: ReactNode): ReactNode {
  if (typeof children === "string") return splitVars(children);
  if (Array.isArray(children)) {
    return children.map((c, i) =>
      typeof c === "string"
        ? <span key={i}>{splitVars(c)}</span>
        : c,
    );
  }
  return children;
}

function splitVars(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const regex = /\{\{\s*(\w+)\s*\}\}/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) out.push(text.slice(lastIndex, match.index));
    out.push(
      <span key={`v-${i++}`} className={styles.variable}>
        {"{{ "}{match[1]}{" }}"}
      </span>,
    );
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out;
}

export default memo(MarkdownPreviewImpl);
