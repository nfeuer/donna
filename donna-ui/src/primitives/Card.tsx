import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Card.module.css";

export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.card, className)} {...rest}>
      {children}
    </div>
  ),
);
Card.displayName = "Card";

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.header, className)} {...rest}>
      {children}
    </div>
  ),
);
CardHeader.displayName = "CardHeader";

export const CardEyebrow = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.eyebrow, className)} {...rest}>
      {children}
    </div>
  ),
);
CardEyebrow.displayName = "CardEyebrow";

export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, children, ...rest }, ref) => (
    <h3 ref={ref} className={cn(styles.title, className)} {...rest}>
      {children}
    </h3>
  ),
);
CardTitle.displayName = "CardTitle";
