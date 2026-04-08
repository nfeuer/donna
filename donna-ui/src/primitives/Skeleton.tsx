import type { CSSProperties } from "react";
import { cn } from "../lib/cn";
import styles from "./Skeleton.module.css";

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  className?: string;
  style?: CSSProperties;
}

export function Skeleton({ width = "100%", height = 14, className, style }: SkeletonProps) {
  return (
    <div
      className={cn(styles.skeleton, className)}
      style={{ width, height, ...style }}
      aria-busy="true"
      aria-live="polite"
    />
  );
}
