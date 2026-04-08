import * as RadixCheckbox from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { useId, type ReactNode } from "react";
import styles from "./Checkbox.module.css";

interface CheckboxProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
}

export function Checkbox({ checked, onCheckedChange, children, disabled }: CheckboxProps) {
  const id = useId();
  return (
    <label htmlFor={id} className={styles.root}>
      <RadixCheckbox.Root
        id={id}
        className={styles.box}
        checked={checked}
        onCheckedChange={(v) => onCheckedChange(v === true)}
        disabled={disabled}
      >
        <RadixCheckbox.Indicator>
          <Check size={11} />
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {children}
    </label>
  );
}
