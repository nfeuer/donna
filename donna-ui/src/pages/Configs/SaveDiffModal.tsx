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
          renderSideBySide: true,
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
