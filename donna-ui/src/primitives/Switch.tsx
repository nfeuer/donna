import * as RadixSwitch from "@radix-ui/react-switch";
import { useId, type ReactNode } from "react";
import styles from "./Switch.module.css";

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children?: ReactNode;
  disabled?: boolean;
}

export function Switch({ checked, onCheckedChange, children, disabled }: SwitchProps) {
  const id = useId();
  const control = (
    <RadixSwitch.Root
      id={id}
      className={styles.root}
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
    >
      <RadixSwitch.Thumb className={styles.thumb} />
    </RadixSwitch.Root>
  );
  if (!children) return control;
  return (
    <label htmlFor={id} className={styles.label}>
      {control}
      {children}
    </label>
  );
}
