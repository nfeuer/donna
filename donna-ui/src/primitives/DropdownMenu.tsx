import * as Radix from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import styles from "./DropdownMenu.module.css";

export function DropdownMenu({ children }: { children: ReactNode }) {
  return <Radix.Root>{children}</Radix.Root>;
}

export function DropdownMenuTrigger({ children }: { children: ReactNode }) {
  return <Radix.Trigger asChild>{children}</Radix.Trigger>;
}

export function DropdownMenuContent({ children }: { children: ReactNode }) {
  return (
    <Radix.Portal>
      <Radix.Content className={styles.content} sideOffset={4} align="end">
        {children}
      </Radix.Content>
    </Radix.Portal>
  );
}

export function DropdownMenuItem({ children, onSelect }: { children: ReactNode; onSelect?: () => void }) {
  return <Radix.Item className={styles.item} onSelect={onSelect}>{children}</Radix.Item>;
}

export function DropdownMenuSeparator() {
  return <Radix.Separator className={styles.separator} />;
}
