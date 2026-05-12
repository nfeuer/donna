import { useId, type ReactNode } from "react";
import { Check, Minus } from "lucide-react";
import styles from "./TriCheckbox.module.css";

export type TriState = "neutral" | "include" | "exclude";

interface TriCheckboxProps {
  state: TriState;
  onCycle: () => void;
  children: ReactNode;
  disabled?: boolean;
}

const NEXT: Record<TriState, TriState> = {
  neutral: "include",
  include: "exclude",
  exclude: "neutral",
};

export function cycleTriState(current: TriState): TriState {
  return NEXT[current];
}

export function TriCheckbox({ state, onCycle, children, disabled }: TriCheckboxProps) {
  const id = useId();
  return (
    <label htmlFor={id} className={styles.root}>
      <button
        id={id}
        type="button"
        role="checkbox"
        aria-checked={state === "include" ? "true" : state === "exclude" ? "mixed" : "false"}
        className={styles.box}
        data-state={state}
        onClick={onCycle}
        disabled={disabled}
      >
        {state === "include" && <Check size={11} />}
        {state === "exclude" && <Minus size={11} />}
      </button>
      {children}
    </label>
  );
}
