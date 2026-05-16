import { MessageSquare } from "lucide-react";
import { useLocation } from "react-router-dom";
import styles from "./QuickChatButton.module.css";

interface Props {
  onClick: () => void;
  visible: boolean;
}

export default function QuickChatButton({ onClick, visible }: Props) {
  const location = useLocation();
  const onChatPage = location.pathname === "/chat";

  if (onChatPage || !visible) return null;

  return (
    <button
      type="button"
      className={styles.fab}
      onClick={onClick}
      aria-label="Open quick chat"
    >
      <MessageSquare size={20} />
    </button>
  );
}
