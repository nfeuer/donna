import { useState, useCallback } from "react";
import { Button } from "../../primitives/Button";
import styles from "./Chat.module.css";

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export default function MessageInput({ onSend, disabled }: Props) {
  const [text, setText] = useState("");

  const handleSubmit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  }, [text, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className={styles.inputArea}>
      <textarea
        className={styles.inputTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Message Donna..."
        rows={2}
        disabled={disabled}
      />
      <Button variant="primary" size="sm" onClick={handleSubmit} disabled={disabled || !text.trim()}>
        Send
      </Button>
    </div>
  );
}
