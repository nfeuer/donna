import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../primitives/Dialog";
import {
  SHORTCUT_DEFINITIONS,
  type ShortcutDef,
} from "../hooks/useKeyboardShortcuts";
import styles from "./KeyboardShortcutsModal.module.css";

const CATEGORIES = ["Navigation", "Actions", "Help"] as const;

function KeyChip({ keys }: { keys: string }) {
  return (
    <span className={styles.keyRow}>
      {keys.split(" ").map((k, i) => (
        <kbd key={i} className={styles.kbd}>
          {k}
        </kbd>
      ))}
    </span>
  );
}

function groupByCategory(defs: ShortcutDef[]) {
  const grouped: Record<string, ShortcutDef[]> = {};
  for (const cat of CATEGORIES) {
    grouped[cat] = defs.filter((d) => d.category === cat);
  }
  return grouped;
}

/**
 * Opens when the window receives a `show-shortcuts-help` event
 * (dispatched by useKeyboardShortcuts on "?" keypress). Closing is
 * handled by Radix Dialog natively (Esc, overlay click, close button).
 * Focus trap + `role="dialog"` come from Radix for free — this resolves
 * the P1 a11y audit issue.
 */
export default function KeyboardShortcutsModal() {
  const [open, setOpen] = useState(false);

  const handleShow = useCallback(() => setOpen(true), []);

  useEffect(() => {
    window.addEventListener("show-shortcuts-help", handleShow);
    return () => {
      window.removeEventListener("show-shortcuts-help", handleShow);
    };
  }, [handleShow]);

  const grouped = groupByCategory(SHORTCUT_DEFINITIONS);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogHeader>
        <DialogTitle>Keyboard Shortcuts</DialogTitle>
        <DialogDescription>
          Press <kbd className={styles.kbd}>?</kbd> any time to reopen this.
        </DialogDescription>
      </DialogHeader>
      <div className={styles.body}>
        {CATEGORIES.map((cat) => (
          <section key={cat} className={styles.group}>
            <h3 className={styles.groupTitle}>{cat}</h3>
            <ul className={styles.list}>
              {grouped[cat].map((def) => (
                <li key={def.keys} className={styles.row}>
                  <span className={styles.desc}>{def.description}</span>
                  <KeyChip keys={def.keys} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
