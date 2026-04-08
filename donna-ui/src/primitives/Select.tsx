import * as RadixSelect from "@radix-ui/react-select";
import { ChevronDown, Check } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Select.module.css";

interface SelectProps {
  value: string;
  onValueChange: (v: string) => void;
  placeholder?: string;
  children: ReactNode;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

export function Select({ value, onValueChange, placeholder, children, ...aria }: SelectProps) {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange}>
      <RadixSelect.Trigger className={styles.trigger} {...aria}>
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className={styles.icon}>
          <ChevronDown size={14} />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className={styles.content} position="popper" sideOffset={4}>
          <RadixSelect.Viewport>{children}</RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}

interface SelectItemProps {
  value: string;
  children: ReactNode;
}

export function SelectItem({ value, children }: SelectItemProps) {
  return (
    <RadixSelect.Item value={value} className={cn(styles.item)}>
      <RadixSelect.ItemText>{children}</RadixSelect.ItemText>
      <RadixSelect.ItemIndicator>
        <Check size={12} />
      </RadixSelect.ItemIndicator>
    </RadixSelect.Item>
  );
}
