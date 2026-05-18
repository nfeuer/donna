import { useState, useEffect } from "react";
import { ChevronRight, Copy, Check } from "lucide-react";
import { fetchClaudePayload, type ClaudePayload } from "../../api/claude";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import styles from "./claude-inspector.module.css";

interface Props {
  invocationId: string;
  hasPayload: boolean;
}

export default function CallDetail({ invocationId, hasPayload }: Props) {
  const [payload, setPayload] = useState<ClaudePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requestOpen, setRequestOpen] = useState(true);
  const [responseOpen, setResponseOpen] = useState(false);
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  useEffect(() => {
    if (!hasPayload) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchClaudePayload(invocationId)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load payload");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [invocationId, hasPayload]);

  const handleCopy = async (section: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedSection(section);
      setTimeout(() => setCopiedSection(null), 2000);
    } catch {
      // Clipboard write failed silently
    }
  };

  if (!hasPayload) {
    return (
      <div className={styles.detail}>
        <div className={styles.detailHeader}>
          <span className={styles.detailTitle}>{invocationId}</span>
        </div>
        <div className={styles.evictedMsg}>
          Payload evicted — only metadata available.
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className={styles.detail}>
        <Skeleton width={200} height={12} />
        <Skeleton width="100%" height={100} style={{ marginTop: 12 }} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.detail}>
        <div className={styles.detailHeader}>
          <span className={styles.detailTitle}>{invocationId}</span>
        </div>
        <div className={styles.evictedMsg}>{error}</div>
      </div>
    );
  }

  const requestJson = JSON.stringify(payload?.request, null, 2) ?? "";
  const responseJson = JSON.stringify(payload?.response, null, 2) ?? "";

  return (
    <div className={styles.detail}>
      <div className={styles.detailHeader}>
        <span className={styles.detailTitle}>{invocationId}</span>
      </div>

      {/* Request Section */}
      <div className={styles.section}>
        <button
          type="button"
          className={styles.sectionToggle}
          onClick={() => setRequestOpen(!requestOpen)}
        >
          <span>Request</span>
          <ChevronRight
            size={14}
            className={cn(
              styles.sectionChevron,
              requestOpen && styles.sectionChevronOpen,
            )}
          />
        </button>
        {requestOpen && (
          <div className={styles.jsonWrap}>
            <pre className={styles.json}>{requestJson}</pre>
            <button
              type="button"
              className={styles.copyBtn}
              onClick={() => handleCopy("request", requestJson)}
            >
              {copiedSection === "request" ? (
                <Check size={12} />
              ) : (
                <Copy size={12} />
              )}
            </button>
          </div>
        )}
      </div>

      {/* Response Section */}
      <div className={styles.section}>
        <button
          type="button"
          className={styles.sectionToggle}
          onClick={() => setResponseOpen(!responseOpen)}
        >
          <span>Response</span>
          <ChevronRight
            size={14}
            className={cn(
              styles.sectionChevron,
              responseOpen && styles.sectionChevronOpen,
            )}
          />
        </button>
        {responseOpen && (
          <div className={styles.jsonWrap}>
            <pre className={styles.json}>{responseJson}</pre>
            <button
              type="button"
              className={styles.copyBtn}
              onClick={() => handleCopy("response", responseJson)}
            >
              {copiedSection === "response" ? (
                <Check size={12} />
              ) : (
                <Copy size={12} />
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
