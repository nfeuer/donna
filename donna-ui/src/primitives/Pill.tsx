import type { HTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Pill.module.css";

export type PillVariant = "accent" | "success" | "warning" | "error" | "muted";

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: PillVariant;
}

export function Pill({ variant = "accent", className, children, ...rest }: PillProps) {
  return (
    <span className={cn(styles.pill, styles[variant], className)} {...rest}>
      {children}
    </span>
  );
}
