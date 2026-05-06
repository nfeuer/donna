import { useState } from "react";
import { Button } from "../../primitives/Button";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../primitives/Dialog";
import { FormField, Input } from "../../primitives/Input";
import { submitEscalation } from "../../api/escalations";

interface Props {
  correlationId: string;
  defaultBranch?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmitted: () => void;
}

const SHA_RE = /^[0-9a-f]{7,40}$/;

/**
 * Slice 21 — opens from the claude_code submission slot on the
 * escalation detail page. POSTs to /admin/escalations/{id}/submit
 * with the discriminated-union claude_code payload.
 *
 * No iteration-cap UI here — the server returns 409 with a
 * structured detail; we surface it inline so the user knows to wait
 * for human review.
 */
export default function MarkAsBuiltModal({
  correlationId,
  defaultBranch,
  open,
  onOpenChange,
  onSubmitted,
}: Props) {
  const [branch, setBranch] = useState(defaultBranch ?? "");
  const [sha, setSha] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setError(null);
    if (!branch.trim()) {
      setError("Branch name is required.");
      return;
    }
    if (sha && !SHA_RE.test(sha)) {
      setError("SHA must be 7–40 hex chars.");
      return;
    }
    setSubmitting(true);
    try {
      await submitEscalation(correlationId, {
        mode: "claude_code",
        branch: branch.trim(),
        ...(sha ? { sha: sha.trim() } : {}),
      });
      onSubmitted();
      onOpenChange(false);
    } catch (err) {
      // Server returns structured detail on 4xx — best-effort extract.
      const axiosErr = err as {
        response?: { data?: { detail?: { error?: string; message?: string } } };
        message?: string;
      };
      const detail = axiosErr.response?.data?.detail;
      if (detail?.error === "iteration_cap_reached") {
        setError(
          "Iteration cap reached. This escalation has been routed to human review.",
        );
      } else if (detail?.error === "not_awaiting_submission") {
        setError("This escalation isn't accepting submissions right now.");
      } else if (detail?.message) {
        setError(detail.message);
      } else if (axiosErr.message) {
        setError(axiosErr.message);
      } else {
        setError("Submission failed.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Mark as built</DialogTitle>
        <DialogDescription>
          Tell Donna which branch your build is on. The poller will diff
          it against the spec scope, run the validator, and either
          promote the skill into sandbox or post failures back to
          Discord for iteration.
        </DialogDescription>
      </DialogHeader>

      <FormField label="Branch name">
        {(props) => (
          <Input
            {...props}
            type="text"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="escalation/abcd1234-foo"
            disabled={submitting}
          />
        )}
      </FormField>

      <FormField
        label="Commit SHA (optional)"
        error={error ?? undefined}
      >
        {(props) => (
          <Input
            {...props}
            type="text"
            value={sha}
            onChange={(e) => setSha(e.target.value)}
            placeholder="(7–40 hex chars; locks validation to this SHA)"
            disabled={submitting}
          />
        )}
      </FormField>

      <DialogFooter>
        <Button
          variant="ghost"
          onClick={() => onOpenChange(false)}
          disabled={submitting}
        >
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={submitting || !branch.trim()}>
          {submitting ? "Submitting…" : "Submit"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
