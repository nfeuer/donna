import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../lib/cn";
import styles from "./NavItem.module.css";

interface NavItemProps {
  to: string;
  icon: ReactNode;
  label: string;
  active: boolean;
}

/**
 * Single rail nav entry. Gold left-border active state per spec §5
 * ("Menu active state: Gold left border only — no background fill").
 * `aria-current="page"` when active so screen readers announce it.
 */
export function NavItem({ to, icon, label, active }: NavItemProps) {
  return (
    <li className={styles.listItem}>
      <Link
        to={to}
        className={cn(styles.link, active && styles.active)}
        aria-current={active ? "page" : undefined}
      >
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
        <span className={styles.label}>{label}</span>
      </Link>
    </li>
  );
}
