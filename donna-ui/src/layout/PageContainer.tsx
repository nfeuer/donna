import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./PageContainer.module.css";

interface PageContainerProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/**
 * Max-width wrapper for page content. Added in Wave 2 but consumed starting
 * in Wave 3 as each page migrates. Purely a max-width constraint — outer
 * padding is provided by AppShell's <main>. Do not add padding here or
 * pages will get double gutters during the transition window.
 */
export function PageContainer({
  children,
  className,
  ...rest
}: PageContainerProps) {
  return (
    <div className={cn(styles.container, className)} {...rest}>
      {children}
    </div>
  );
}
