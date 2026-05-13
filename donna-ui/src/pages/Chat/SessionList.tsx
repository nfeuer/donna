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

export default function SessionList({ sessions, selectedId, onSelect }: Props) {
  if (sessions.length === 0) {
    return <div className={styles.sessionListEmpty}>No sessions yet. Send a message to start.</div>;
  }

  return (
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
          <div className={styles.sessionItemTime}>{new Date(s.last_activity).toLocaleString()}</div>
        </li>
      ))}
    </ul>
  );
}
