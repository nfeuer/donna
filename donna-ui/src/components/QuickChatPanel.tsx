import { useState, useCallback, useEffect } from "react";
import { X } from "lucide-react";
import {
  sendMessage,
  fetchContextStatus,
  type ChatMessage,
  type ChatResponse,
  type ContextStatus,
} from "../api/chat";
import { useDashboardContext } from "../context/DashboardContext";
import MessageThread from "../pages/Chat/MessageThread";
import MessageInput from "../pages/Chat/MessageInput";
import styles from "./QuickChatPanel.module.css";

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

  useEffect(() => {
    if (!open) return;
    setSessionId(null);
    setMessages([]);
    setLastResponse(null);
    setContextStatus(null);
  }, [open]);

  const handleSend = useCallback(
    async (text: string) => {
      const sid = sessionId || "new";
      setSending(true);
      try {
        const context = {
          page: currentPage,
          selected_item: selectedItem
            ? { type: selectedItem.type, id: selectedItem.id, label: selectedItem.label }
            : null,
        };
        const resp = await sendMessage(sid, text, "dashboard_quick", context);
        setLastResponse(resp);

        if (resp.session_id && !sessionId) {
          setSessionId(resp.session_id);
        }

        const loadId = resp.session_id || sessionId;
        if (loadId) {
          const { fetchSession } = await import("../api/chat");
          const session = await fetchSession(loadId);
          setMessages(session.messages);
          const ctx = await fetchContextStatus(loadId);
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
          {messages.length > 0 ? (
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
            <MessageInput onSend={handleSend} disabled={sending} />
          </div>
        </div>
      </div>
    </>
  );
}
