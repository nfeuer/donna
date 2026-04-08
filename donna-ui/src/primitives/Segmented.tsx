import { cn } from "../lib/cn";
import styles from "./Segmented.module.css";

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  value: T;
  onValueChange: (v: T) => void;
  options: SegmentedOption<T>[];
  "aria-label"?: string;
}

export function Segmented<T extends string>({
  value,
  onValueChange,
  options,
  ...aria
}: SegmentedProps<T>) {
  return (
    <div className={styles.root} role="tablist" {...aria}>
      {options.map((opt) => (
        <button
          key={opt.value}
          role="tab"
          aria-selected={value === opt.value}
          className={cn(styles.item, value === opt.value && styles.active)}
          onClick={() => onValueChange(opt.value)}
          type="button"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
