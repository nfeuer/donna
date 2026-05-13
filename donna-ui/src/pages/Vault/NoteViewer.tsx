import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import type { VaultNote } from "../../api/vault";
import styles from "./Vault.module.css";

interface Props {
  note: VaultNote | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function NoteViewer({ note, open, onOpenChange }: Props) {
  if (!note) return null;

  const modified = new Date(note.mtime * 1000).toLocaleString();
  const sizeKb = (note.size / 1024).toFixed(1);

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title={note.path}>
      <div className={styles.noteHeader}>
        <span className={styles.noteMeta}>{modified} — {sizeKb} KB</span>
      </div>
      {Object.keys(note.frontmatter).length > 0 && (
        <div className={styles.noteFrontmatter}>
          {Object.entries(note.frontmatter).map(([key, value]) => (
            <Pill key={key} variant="muted">{key}: {String(value)}</Pill>
          ))}
        </div>
      )}
      <pre className={styles.noteContent}>{note.content}</pre>
    </Drawer>
  );
}
