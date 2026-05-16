import { useState, useCallback, useEffect } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import {
  sendMessage,
  fetchSession,
  fetchContextStatus,
  escalateSession,
  listSessions,
  type ChatSession,
  type ChatMessage,
  type ChatResponse,
  type ContextStatus,
} from "../../api/chat";
import SessionList from "./SessionList";
import MessageThread from "./MessageThread";
import MessageInput from "./MessageInput";
import ContextMeter from "./ContextMeter";
import styles from "./Chat.module.css";

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [sending, setSending] = useState(false);

  const refreshSessions = useCallback(async () => {
    try {
      const result = await listSessions({ limit: 50 });
      setSessions(result.sessions);
    } catch {
      // Error toast handled by global interceptor
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const loadSession = useCallback(async (sessionId: string) => {
    try {
      const resp = await fetchSession(sessionId);
      setMessages(resp.messages);
      setActiveSessionId(sessionId);
      const ctx = await fetchContextStatus(sessionId);
      setContextStatus(ctx);
    } catch {
      setMessages([]);
      setContextStatus(null);
    }
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      const sid = activeSessionId || "new";
      setSending(true);
      try {
        const resp = await sendMessage(sid, text);
        setLastResponse(resp);
        const resolvedId = resp.session_id ?? (sid !== "new" ? sid : null);
        if (resolvedId) {
          if (!activeSessionId) {
            setActiveSessionId(resolvedId);
          }
          await loadSession(resolvedId);
          await refreshSessions();
        }
      } catch {
        // Error toast handled by global interceptor
      } finally {
        setSending(false);
      }
    },
    [activeSessionId, loadSession, refreshSessions],
  );

  const handleEscalate = useCallback(async () => {
    if (!activeSessionId) return;
    try {
      const resp = await escalateSession(activeSessionId);
      setLastResponse(resp);
      await loadSession(activeSessionId);
    } catch {
      // handled by interceptor
    }
  }, [activeSessionId, loadSession]);

  const handleActionClick = useCallback(
    (action: string) => { handleSend(action); },
    [handleSend],
  );

  const handleNewSession = useCallback(() => {
    setActiveSessionId(null);
    setMessages([]);
    setLastResponse(null);
    setContextStatus(null);
  }, []);

  return (
    <div>
      <PageHeader
        eyebrow="Conversation"
        title="Chat"
        actions={
          <Button variant="primary" size="sm" onClick={handleNewSession}>
            New Session
          </Button>
        }
      />
      <div className={styles.chatLayout}>
        <SessionList sessions={sessions} selectedId={activeSessionId} onSelect={loadSession} />
        <div className={styles.conversationPanel}>
          {activeSessionId && <ContextMeter status={contextStatus} />}
          {messages.length > 0 ? (
            <MessageThread
              messages={messages}
              lastResponse={lastResponse}
              onEscalate={handleEscalate}
              onActionClick={handleActionClick}
            />
          ) : (
            <div className={styles.emptyConversation}>
              Send a message to start a conversation with Donna.
            </div>
          )}
          <MessageInput onSend={handleSend} disabled={sending} />
        </div>
      </div>
    </div>
  );
}
