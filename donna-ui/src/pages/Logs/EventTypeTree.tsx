import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import { fetchEventTypes } from "../../api/logs";
import { TriCheckbox, cycleTriState, type TriState } from "./TriCheckbox";
import styles from "./EventTypeTree.module.css";

export type EventFilterMode = "include" | "exclude";
export type EventFilterMap = Record<string, EventFilterMode>;

interface Props {
  filters: EventFilterMap;
  onChange: (filters: EventFilterMap) => void;
}

export default function EventTypeTree({ filters, onChange }: Props) {
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

  const cycleLeaf = (key: string) => {
    const current: TriState = filters[key] ?? "neutral";
    const next = cycleTriState(current);
    const updated = { ...filters };
    if (next === "neutral") {
      delete updated[key];
    } else {
      updated[key] = next;
    }
    onChange(updated);
  };

  const toggleCategory = (category: string) => {
    const next = new Set(collapsed);
    if (next.has(category)) next.delete(category);
    else next.add(category);
    setCollapsed(next);
  };

  const clearAll = () => onChange({});

  const includeAll = () => {
    const next: EventFilterMap = {};
    for (const key of allKeys) next[key] = "include";
    onChange(next);
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
        <Button variant="ghost" size="sm" onClick={includeAll}>
          All
        </Button>
        <Button variant="ghost" size="sm" onClick={clearAll}>
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
                      const state: TriState = filters[key] ?? "neutral";
                      return (
                        <li
                          key={key}
                          className={cn(
                            styles.leaf,
                            state === "include" && styles.leafInclude,
                            state === "exclude" && styles.leafExclude,
                          )}
                        >
                          <TriCheckbox state={state} onCycle={() => cycleLeaf(key)}>
                            <span className={styles.leafText}>{evt}</span>
                          </TriCheckbox>
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
