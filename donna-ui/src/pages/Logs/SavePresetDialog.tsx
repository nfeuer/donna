import { useEffect, useState } from "react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../primitives/Dialog";
import { Button } from "../../primitives/Button";
import { FormField, Input } from "../../primitives/Input";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (name: string) => void;
}

/**
 * Thin Dialog wrapper that captures a preset name. Submits on Enter
 * or the Save button; disabled while the name is empty.
 */
export function SavePresetDialog({ open, onOpenChange, onSave }: Props) {
  const [name, setName] = useState("");

  useEffect(() => {
    if (!open) setName("");
  }, [open]);

  const canSave = name.trim().length > 0;

  const submit = () => {
    if (!canSave) return;
    onSave(name.trim());
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Save filter preset</DialogTitle>
        <DialogDescription>
          Save the current event types, level, and search query under a name you'll remember.
        </DialogDescription>
      </DialogHeader>
      <FormField label="Preset name">
        {(props) => (
          <Input
            {...props}
            placeholder="e.g. Agent errors last 24h"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            autoFocus
          />
        )}
      </FormField>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={!canSave}>
          Save preset
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
