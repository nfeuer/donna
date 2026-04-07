import { useState, useEffect, useCallback } from "react";
import { Modal, Typography } from "antd";
import {
  SHORTCUT_DEFINITIONS,
  type ShortcutDef,
} from "../hooks/useKeyboardShortcuts";

const { Text } = Typography;

const categories = ["Navigation", "Actions", "Help"] as const;

function KeyChip({ keys }: { keys: string }) {
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      {keys.split(" ").map((k, i) => (
        <kbd
          key={i}
          style={{
            display: "inline-block",
            padding: "2px 6px",
            fontSize: 12,
            fontFamily: "monospace",
            lineHeight: "18px",
            color: "#d9d9d9",
            background: "#303030",
            border: "1px solid #434343",
            borderRadius: 4,
            minWidth: 20,
            textAlign: "center",
          }}
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

function groupByCategory(defs: ShortcutDef[]) {
  const grouped: Record<string, ShortcutDef[]> = {};
  for (const cat of categories) {
    grouped[cat] = defs.filter((d) => d.category === cat);
  }
  return grouped;
}

export default function KeyboardShortcutsModal() {
  const [open, setOpen] = useState(false);

  const handleShow = useCallback(() => setOpen(true), []);
  const handleClose = useCallback(() => setOpen(false), []);

  useEffect(() => {
    window.addEventListener("show-shortcuts-help", handleShow);
    window.addEventListener("close-drawer", handleClose);
    return () => {
      window.removeEventListener("show-shortcuts-help", handleShow);
      window.removeEventListener("close-drawer", handleClose);
    };
  }, [handleShow, handleClose]);

  const grouped = groupByCategory(SHORTCUT_DEFINITIONS);

  return (
    <Modal
      title="Keyboard Shortcuts"
      open={open}
      onCancel={handleClose}
      footer={null}
      width={420}
    >
      {categories.map((cat) => (
        <div key={cat} style={{ marginBottom: 16 }}>
          <Text strong style={{ fontSize: 13, color: "#8c8c8c" }}>
            {cat}
          </Text>
          <div style={{ marginTop: 8 }}>
            {grouped[cat].map((def) => (
              <div
                key={def.keys}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "4px 0",
                }}
              >
                <span style={{ fontSize: 13 }}>{def.description}</span>
                <KeyChip keys={def.keys} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </Modal>
  );
}
