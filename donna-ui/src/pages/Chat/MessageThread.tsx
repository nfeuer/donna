import { useRef, useEffect } from "react";
import { Bug } from "lucide-react";
import { Pill } from "../../primitives/Pill";
import type { ChatMessage, ChatResponse } from "../../api/chat";
import styles from "./Chat.module.css";

interface Props {
  messages: ChatMessage[];
  lastResponse: ChatResponse | null;
  onEscalate: () => void;
  onActionClick: (action: string) => void;
}

export default function MessageThread({ messages, lastResponse, onEscalate, onActionClick }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className={styles.threadContainer}>
      {messages.map((msg) => (
        <div key={msg.id} className={msg.role === "user" ? styles.msgUser : styles.msgAssistant}>
          <div className={styles.msgBubble}>
            <div className={styles.msgHeader}>
              <Pill variant={msg.role === "user" ? "accent" : "muted"}>{msg.role}</Pill>
              {msg.intent && <Pill variant="muted">{msg.intent}</Pill>}
              <span className={styles.msgTime}>{new Date(msg.created_at).toLocaleTimeString()}</span>
              {msg.role === "assistant" && msg.trace_id && (
                <a
                  href={`/claude-inspector?trace_id=${msg.trace_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.debugLink}
                  title="View in Inspector"
                >
                  <Bug size={12} />
                </a>
              )}
            </div>
            <div className={styles.msgContent}>{msg.content}</div>
          </div>
        </div>
      ))}

      {lastResponse?.needs_escalation && (
        <div className={styles.escalationBanner}>
          <strong>Escalation needed:</strong> {lastResponse.escalation_reason}
          <button className={styles.escalateBtn} onClick={onEscalate} type="button">
            Approve Escalation
          </button>
        </div>
      )}

      {lastResponse?.suggested_actions && lastResponse.suggested_actions.length > 0 && (
        <div className={styles.suggestedActions}>
          {lastResponse.suggested_actions.map((action) => (
            <Pill key={action} variant="accent" onClick={() => onActionClick(action)} style={{ cursor: "pointer" }}>
              {action}
            </Pill>
          ))}
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}
