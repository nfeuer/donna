import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "ghost" | "text";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", className, children, ...rest }, ref) => (
    <button
      ref={ref}
      className={cn(
        styles.button,
        styles[variant],
        size === "sm" && styles.sm,
        size === "lg" && styles.lg,
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  ),
);
Button.displayName = "Button";
