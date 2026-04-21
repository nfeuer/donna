import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../primitives/Button";
import { Select, SelectItem } from "../../primitives/Select";
import { Textarea } from "../../primitives/Input";
import {
  transitionSkillState,
  type TransitionRow,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  skillId: string;
  currentState: string;
  transitions: TransitionRow[];
  onSuccess: () => void;
}

export default function StateTransitionForm({
  skillId,
  currentState,
  transitions,
  onSuccess,
}: Props) {
  const [toState, setToState] = useState("");
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toStateOptions = useMemo(
    () =>
      Array.from(
        new Set(
          transitions
            .filter((t) => t.from_state === currentState)
            .map((t) => t.to_state),
        ),
      ).sort(),
    [transitions, currentState],
  );

  const reasonOptions = useMemo(() => {
    if (!toState) return [];
    const row = transitions.find(
      (t) => t.from_state === currentState && t.to_state === toState,
    );
    return row ? row.allowed_reasons : [];
  }, [transitions, currentState, toState]);

  // Clear dependent fields when parent changes.
  useEffect(() => {
    setToState("");
    setReason("");
  }, [currentState]);

  useEffect(() => {
    setReason("");
  }, [toState]);

  const handleSubmit = async () => {
    if (!toState || !reason) {
      setError("Select both a destination state and a reason.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await transitionSkillState(skillId, {
        to_state: toState,
        reason,
        notes: notes.trim() ? notes.trim() : null,
      });
      toast.success("State transition recorded", {
        description: `${currentState} → ${toState} (${reason})`,
      });
      setNotes("");
      setToState("");
      setReason("");
      onSuccess();
    } catch (err: unknown) {
      const message =
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail
          ? (err as { response: { data: { detail: string } } }).response.data
              .detail
          : "Transition failed.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  if (toStateOptions.length === 0) {
    return (
      <p className={styles.kvValue} style={{ color: "var(--color-text-muted)" }}>
        No transitions available from state <code>{currentState}</code>.
      </p>
    );
  }

  return (
    <div className={styles.drawerBody} style={{ gap: "var(--space-3)" }}>
      <div className={styles.formRow}>
        <label className={styles.formLabel}>Destination state</label>
        <Select
          value={toState}
          onValueChange={setToState}
          placeholder="Select state"
          aria-label="Destination state"
        >
          {toStateOptions.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </Select>
      </div>
      <div className={styles.formRow}>
        <label className={styles.formLabel}>Reason</label>
        <Select
          value={reason}
          onValueChange={setReason}
          placeholder={
            toState ? "Select reason" : "Pick a destination state first"
          }
          aria-label="Transition reason"
        >
          {reasonOptions.map((r) => (
            <SelectItem key={r} value={r}>
              {r}
            </SelectItem>
          ))}
        </Select>
      </div>
      <div className={styles.formRow}>
        <label className={styles.formLabel} htmlFor="transition-notes">
          Notes (optional)
        </label>
        <Textarea
          id="transition-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="Why are you making this change?"
        />
      </div>
      {error && <div className={styles.formError}>{error}</div>}
      <div className={styles.actions}>
        <Button
          onClick={handleSubmit}
          disabled={submitting || !toState || !reason}
        >
          {submitting ? "Submitting…" : "Submit transition"}
        </Button>
      </div>
    </div>
  );
}
