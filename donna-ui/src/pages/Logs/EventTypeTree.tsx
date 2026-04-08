import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Checkbox } from "../../primitives/Checkbox";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import { fetchEventTypes } from "../../api/logs";
import styles from "./EventTypeTree.module.css";

interface Props {
  selected: string[];
  onChange: (selected: string[]) => void;
}

/**
 * Sidebar event-type picker. Categories are collapsible; inside each
 * category every event is a Checkbox primitive. Key format matches the
 * AntD Tree version byte-for-byte: `${category}.${event}`, so the API
 * filter string (joined with commas) is unchanged.
 */
export default function EventTypeTree({ selected, onChange }: Props) {
  const [tree, setTree] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchEventTypes()
      .then((data) => setTree(data ?? {}))
      .catch(() => setTree({}))
      .finally(() => setLoading(false));
  }, []);

  const allKeys = useMemo(
    () =>
      Object.entries(tree).flatMap(([cat, evts]) =>
        evts.map((e) => `${cat}.${e}`),
      ),
    [tree],
  );

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const toggleLeaf = (key: string) => {
    const next = new Set(selectedSet);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(Array.from(next));
  };

  const toggleCategory = (category: string) => {
    const next = new Set(collapsed);
    if (next.has(category)) next.delete(category);
    else next.add(category);
    setCollapsed(next);
  };

  if (loading) {
    return (
      <div className={styles.root}>
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
      </div>
    );
  }

  const categories = Object.entries(tree);

  return (
    <div className={styles.root}>
      <div className={styles.actions}>
        <Button variant="ghost" size="sm" onClick={() => onChange(allKeys)}>
          All
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onChange([])}>
          Clear
        </Button>
      </div>

      {categories.length === 0 ? (
        <div className={styles.emptyHint}>No event types registered.</div>
      ) : (
        <ul className={styles.list}>
          {categories.map(([category, events]) => {
            const isCollapsed = collapsed.has(category);
            return (
              <li key={category} className={styles.group}>
                <button
                  type="button"
                  className={styles.groupHeader}
                  onClick={() => toggleCategory(category)}
                  aria-expanded={!isCollapsed}
                >
                  <span className={styles.chevron} aria-hidden="true">
                    {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  </span>
                  <span className={styles.groupLabel}>{category}</span>
                  <span className={styles.groupCount}>{events.length}</span>
                </button>
                {!isCollapsed && (
                  <ul className={styles.children}>
                    {events.map((evt) => {
                      const key = `${category}.${evt}`;
                      const checked = selectedSet.has(key);
                      return (
                        <li key={key} className={cn(styles.leaf, checked && styles.leafActive)}>
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleLeaf(key)}
                          >
                            <span className={styles.leafText}>{evt}</span>
                          </Checkbox>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
