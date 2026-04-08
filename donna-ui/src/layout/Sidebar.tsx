import { useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  ScrollText,
  CheckSquare,
  Bot,
  Settings,
  FileText,
  FlaskConical,
  Lightbulb,
} from "lucide-react";
import { NavItem } from "./NavItem";
import { useTheme } from "../hooks/useTheme";
import { cn } from "../lib/cn";
import styles from "./Sidebar.module.css";

interface NavEntry {
  path: string;
  label: string;
  icon: React.ReactNode;
}

const NAV_ITEMS: NavEntry[] = [
  { path: "/", label: "Dashboard", icon: <LayoutDashboard size={18} /> },
  { path: "/logs", label: "Logs", icon: <ScrollText size={18} /> },
  { path: "/tasks", label: "Tasks", icon: <CheckSquare size={18} /> },
  { path: "/agents", label: "Agents", icon: <Bot size={18} /> },
  { path: "/configs", label: "Configs", icon: <Settings size={18} /> },
  { path: "/prompts", label: "Prompts", icon: <FileText size={18} /> },
  { path: "/shadow", label: "Shadow", icon: <FlaskConical size={18} /> },
  { path: "/preferences", label: "Preferences", icon: <Lightbulb size={18} /> },
];

function isActive(pathname: string, itemPath: string): boolean {
  // "/" matches only exactly so nested routes don't light up Dashboard.
  if (itemPath === "/") return pathname === "/";
  return pathname === itemPath || pathname.startsWith(`${itemPath}/`);
}

/**
 * Left rail. Fixed 220 px. Brand wordmark at top, NAV_ITEMS in the middle,
 * theme toggle chips + shortcut hint at the bottom. No collapse behaviour —
 * spec §5 specifies a fixed rail with gold left-border active state.
 */
export function Sidebar() {
  const location = useLocation();
  const { theme, setTheme } = useTheme();

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>
        <div className={styles.brandName}>Donna</div>
        <div className={styles.brandEyebrow}>Executive Console</div>
      </div>

      <nav className={styles.nav} aria-label="Primary navigation">
        <ul className={styles.navList}>
          {NAV_ITEMS.map((item) => (
            <NavItem
              key={item.path}
              to={item.path}
              icon={item.icon}
              label={item.label}
              active={isActive(location.pathname, item.path)}
            />
          ))}
        </ul>
      </nav>

      <div className={styles.footer}>
        <div className={styles.themeRow} role="group" aria-label="Accent theme">
          <button
            type="button"
            aria-label="Champagne gold theme"
            aria-pressed={theme === "gold"}
            className={cn(
              styles.themeChip,
              styles.themeChipGold,
              theme === "gold" && styles.themeChipActive,
            )}
            onClick={() => setTheme("gold")}
          >
            Gold
          </button>
          <button
            type="button"
            aria-label="Electric coral theme"
            aria-pressed={theme === "coral"}
            className={cn(
              styles.themeChip,
              styles.themeChipCoral,
              theme === "coral" && styles.themeChipActive,
            )}
            onClick={() => setTheme("coral")}
          >
            Coral
          </button>
        </div>
        <div className={styles.shortcutHint} aria-hidden="true">
          <kbd className={styles.kbd}>⌘.</kbd>
          <span>to flip</span>
        </div>
      </div>
    </aside>
  );
}
