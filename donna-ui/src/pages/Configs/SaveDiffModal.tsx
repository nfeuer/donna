import { useEffect, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../primitives/Dialog";
import { Button } from "../../primitives/Button";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";

const SIDE_BY_SIDE_QUERY = "(min-width: 900px)";

function useSideBySide(): boolean {
  const [sideBySide, setSideBySide] = useState(() =>
    typeof window === "undefined" ? true : window.matchMedia(SIDE_BY_SIDE_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(SIDE_BY_SIDE_QUERY);
    const handler = (e: MediaQueryListEvent) => setSideBySide(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return sideBySide;
}

interface Props {
  open: boolean;
  original: string;
  modified: string;
  filename: string;
  saving: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function SaveDiffModal({
  open,
  original,
  modified,
  filename,
  saving,
  onConfirm,
  onCancel,
}: Props) {
  const sideBySide = useSideBySide();
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }} size="wide">
      <DialogHeader>
        <DialogTitle>Save changes to {filename}?</DialogTitle>
        <DialogDescription>
          Review the diff — left is on disk, right is your edits.
        </DialogDescription>
      </DialogHeader>

      <DiffEditor
        height="min(60vh, 480px)"
        language="yaml"
        theme={DONNA_MONACO_THEME}
        beforeMount={setupDonnaMonacoTheme}
        original={original}
        modified={modified}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          scrollBeyondLastLine: false,
          renderSideBySide: sideBySide,
        }}
      />

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={onConfirm} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
