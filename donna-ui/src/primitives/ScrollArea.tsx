import * as Radix from "@radix-ui/react-scroll-area";
import type { CSSProperties, ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./ScrollArea.module.css";

interface ScrollAreaProps {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function ScrollArea({ children, className, style }: ScrollAreaProps) {
  return (
    <Radix.Root className={cn(styles.root, className)} style={style}>
      <Radix.Viewport className={styles.viewport}>{children}</Radix.Viewport>
      <Radix.Scrollbar className={styles.scrollbar} orientation="vertical">
        <Radix.Thumb className={styles.thumb} />
      </Radix.Scrollbar>
      <Radix.Scrollbar className={styles.scrollbar} orientation="horizontal">
        <Radix.Thumb className={styles.thumb} />
      </Radix.Scrollbar>
      <Radix.Corner />
    </Radix.Root>
  );
}
