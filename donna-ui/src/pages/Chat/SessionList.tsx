import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Pill } from "../../primitives/Pill";
import type { PillVariant } from "../../primitives/Pill";
import type { ChatSession } from "../../api/chat";
import styles from "./Chat.module.css";

interface Props {
  sessions: ChatSession[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const STATUS_VARIANT: Record<string, PillVariant> = {
  active: "success",
  expired: "muted",
  closed: "muted",
};

const QUICK_CHANNELS = new Set(["dashboard_quick"]);

function SessionGroup({
  label,
  sessions,
  selectedId,
  onSelect,
  defaultExpanded,
}: {
  label: string;
  sessions: ChatSession[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const Icon = expanded ? ChevronDown : ChevronRight;

  return (
    <div className={styles.sessionGroup}>
      <button
        type="button"
        className={styles.sessionGroupHeader}
        onClick={() => setExpanded((prev) => !prev)}
      >
        <Icon size={14} />
        <span className={styles.sessionGroupLabel}>{label}</span>
        <span className={styles.sessionGroupCount}>{sessions.length}</span>
      </button>
      {expanded && (
        <ul className={styles.sessionList}>
          {sessions.map((s) => (
            <li
              key={s.id}
              className={`${styles.sessionItem} ${s.id === selectedId ? styles.sessionItemActive : ""}`}
              onClick={() => onSelect(s.id)}
            >
              <div className={styles.sessionItemHeader}>
                <Pill variant={STATUS_VARIANT[s.status] ?? "muted"}>{s.status}</Pill>
                <span className={styles.sessionItemCount}>{s.message_count} msgs</span>
              </div>
              <div className={styles.sessionItemTime}>
                {new Date(s.last_activity).toLocaleString()}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function SessionList({ sessions, selectedId, onSelect }: Props) {
  const chatSessions = sessions.filter((s) => !QUICK_CHANNELS.has(s.channel));
  const quickSessions = sessions.filter((s) => QUICK_CHANNELS.has(s.channel));

  if (sessions.length === 0) {
    return <div className={styles.sessionListEmpty}>No sessions yet. Send a message to start.</div>;
  }

  return (
    <div className={styles.sessionGroups}>
      <SessionGroup
        label="Chat"
        sessions={chatSessions}
        selectedId={selectedId}
        onSelect={onSelect}
        defaultExpanded
      />
      <SessionGroup
        label="Quick Chat"
        sessions={quickSessions}
        selectedId={selectedId}
        onSelect={onSelect}
        defaultExpanded={false}
      />
    </div>
  );
}
