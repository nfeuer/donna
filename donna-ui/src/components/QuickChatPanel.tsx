import { useState, useCallback, useEffect, useRef } from "react";
import { X, Plus } from "lucide-react";
import {
  sendMessage,
  fetchSession,
  fetchContextStatus,
  type ChatMessage,
  type ChatResponse,
  type ContextStatus,
} from "../api/chat";
import { useDashboardContext } from "../context/DashboardContext";
import MessageThread from "../pages/Chat/MessageThread";
import MessageInput from "../pages/Chat/MessageInput";
import styles from "./QuickChatPanel.module.css";

const STORAGE_KEY = "donna_quick_chat_sessions";

function getPageSession(page: string): string | null {
  try {
    const map = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}");
    return map[page] ?? null;
  } catch {
    return null;
  }
}

function setPageSession(page: string, sessionId: string): void {
  try {
    const map = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}");
    map[page] = sessionId;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // storage full or unavailable
  }
}

function clearPageSession(page: string): void {
  try {
    const map = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}");
    delete map[page];
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // storage full or unavailable
  }
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function QuickChatPanel({ open, onClose }: Props) {
  const { currentPage, selectedItem, setSelectedItem } = useDashboardContext();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const restoredPageRef = useRef<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (restoredPageRef.current === currentPage) return;
    restoredPageRef.current = currentPage;

    const saved = getPageSession(currentPage);
    if (saved) {
      setSessionId(saved);
      setLoading(true);
      fetchSession(saved)
        .then((s) => {
          setMessages(s.messages);
          return fetchContextStatus(saved);
        })
        .then((ctx) => setContextStatus(ctx))
        .catch(() => {
          clearPageSession(currentPage);
          setSessionId(null);
          setMessages([]);
          setContextStatus(null);
        })
        .finally(() => setLoading(false));
    } else {
      setSessionId(null);
      setMessages([]);
      setLastResponse(null);
      setContextStatus(null);
    }
  }, [open, currentPage]);

  const handleNewSession = useCallback(() => {
    clearPageSession(currentPage);
    restoredPageRef.current = currentPage;
    setSessionId(null);
    setMessages([]);
    setLastResponse(null);
    setContextStatus(null);
  }, [currentPage]);

  const handleSend = useCallback(
    async (text: string) => {
      const sid = sessionId || "new";
      setSending(true);

      const optimisticMsg: ChatMessage = {
        id: `optimistic-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticMsg]);

      try {
        const context = {
          page: currentPage,
          selected_item: selectedItem
            ? { type: selectedItem.type, id: selectedItem.id, label: selectedItem.label }
            : null,
        };
        const resp = await sendMessage(sid, text, "dashboard_quick", context);
        setLastResponse(resp);

        const resolvedId = resp.session_id || sessionId;
        if (resolvedId) {
          if (!sessionId) {
            setSessionId(resolvedId);
          }
          setPageSession(currentPage, resolvedId);
          const session = await fetchSession(resolvedId);
          setMessages(session.messages);
          const ctx = await fetchContextStatus(resolvedId);
          setContextStatus(ctx);
        }
      } catch {
        // Error toast handled by interceptor
      } finally {
        setSending(false);
      }
    },
    [sessionId, currentPage, selectedItem],
  );

  const handleEscalate = useCallback(async () => {
    if (!sessionId) return;
    try {
      const { escalateSession } = await import("../api/chat");
      const resp = await escalateSession(sessionId);
      setLastResponse(resp);
    } catch {
      // handled by interceptor
    }
  }, [sessionId]);

  const handleActionClick = useCallback(
    (action: string) => { handleSend(action); },
    [handleSend],
  );

  const handleChipClick = useCallback(() => {
    setSelectedItem(null);
  }, [setSelectedItem]);

  const contextLabel = selectedItem
    ? `Viewing: ${selectedItem.label}`
    : `Viewing: ${currentPage.charAt(0).toUpperCase() + currentPage.slice(1)}`;

  const tokenLabel = contextStatus
    ? `${(contextStatus.used_tokens / 1000).toFixed(1)}k / ${(contextStatus.max_tokens / 1000).toFixed(0)}k`
    : null;

  if (!open) return null;

  return (
    <>
      <div className={styles.overlay} onClick={onClose} />
      <div className={styles.panel}>
        <div className={styles.header}>
          <span className={styles.headerTitle}>Quick Chat</span>
          <button
            type="button"
            className={styles.newBtn}
            onClick={handleNewSession}
            aria-label="New session"
            title="New session"
          >
            <Plus size={16} />
          </button>
          <button type="button" className={styles.closeBtn} onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className={styles.contextRow}>
          <button type="button" className={styles.contextChip} onClick={handleChipClick}>
            <span className={styles.contextDot} />
            {contextLabel}
          </button>
          {tokenLabel && <span className={styles.tokenCount}>{tokenLabel}</span>}
        </div>
        <div className={styles.body}>
          {loading ? (
            <div className={styles.emptyState}>Loading session...</div>
          ) : messages.length > 0 ? (
            <MessageThread
              messages={messages}
              lastResponse={lastResponse}
              onEscalate={handleEscalate}
              onActionClick={handleActionClick}
            />
          ) : (
            <div className={styles.emptyState}>
              Ask Donna anything about this page.
            </div>
          )}
          <div className={styles.inputWrap}>
            <MessageInput onSend={handleSend} disabled={sending || loading} />
          </div>
        </div>
      </div>
    </>
  );
}
