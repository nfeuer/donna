import * as Radix from "@radix-ui/react-popover";
import type { ReactNode } from "react";
import styles from "./Popover.module.css";

export function Popover({ children }: { children: ReactNode }) {
  return <Radix.Root>{children}</Radix.Root>;
}

export function PopoverTrigger({ children }: { children: ReactNode }) {
  return <Radix.Trigger asChild>{children}</Radix.Trigger>;
}

export function PopoverContent({ children }: { children: ReactNode }) {
  return (
    <Radix.Portal>
      <Radix.Content className={styles.content} sideOffset={6} collisionPadding={8}>
        {children}
        <Radix.Arrow className={styles.arrow} />
      </Radix.Content>
    </Radix.Portal>
  );
}
