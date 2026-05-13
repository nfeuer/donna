import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Input } from "../../primitives/Input";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import { fetchPrompts, type PromptFile } from "../../api/configs";
import styles from "./PromptSidebar.module.css";

interface Props {
  selected: string | null;
}

interface FolderGroup {
  folder: string;
  files: PromptFile[];
}

function groupByFolder(files: PromptFile[]): { groups: FolderGroup[]; root: PromptFile[] } {
  const folders = new Map<string, PromptFile[]>();
  const root: PromptFile[] = [];

  for (const f of files) {
    const sep = f.name.lastIndexOf("/");
    if (sep === -1) {
      root.push(f);
    } else {
      const folder = f.name.slice(0, sep);
      const existing = folders.get(folder);
      if (existing) existing.push(f);
      else folders.set(folder, [f]);
    }
  }

  const groups = Array.from(folders.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([folder, files]) => ({ folder, files }));

  return { groups, root };
}

function stripMd(name: string): string {
  const base = name.includes("/") ? name.slice(name.lastIndexOf("/") + 1) : name;
  return base.replace(/\.md$/, "");
}

export default function PromptSidebar({ selected }: Props) {
  const [files, setFiles] = useState<PromptFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLoading(true);
    fetchPrompts()
      .then(setFiles)
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    if (!search) return files;
    const q = search.toLowerCase();
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, search]);

  const { groups, root } = useMemo(() => groupByFolder(filtered), [filtered]);

  const toggleGroup = (folder: string) => {
    const next = new Set(collapsed);
    if (next.has(folder)) next.delete(folder);
    else next.add(folder);
    setCollapsed(next);
  };

  if (loading) {
    return (
      <aside className={styles.root} aria-label="Prompt templates">
        <div className={styles.title}>Prompt Templates</div>
        <Skeleton height={28} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
      </aside>
    );
  }

  return (
    <aside className={styles.root} aria-label="Prompt templates">
      <div className={styles.title}>Prompt Templates</div>
      <Input
        type="search"
        placeholder="Filter…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className={styles.search}
        aria-label="Filter prompt templates"
      />

      <ul className={styles.list}>
        {groups.map(({ folder, files: groupFiles }) => {
          const isCollapsed = collapsed.has(folder);
          return (
            <li key={folder}>
              <button
                type="button"
                className={styles.groupHeader}
                onClick={() => toggleGroup(folder)}
                aria-expanded={!isCollapsed}
              >
                <span className={styles.chevron} aria-hidden="true">
                  {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                </span>
                <span className={styles.groupLabel}>{folder}</span>
                <span className={styles.groupCount}>{groupFiles.length}</span>
              </button>
              {!isCollapsed && (
                <ul className={styles.children}>
                  {groupFiles.map((f) => (
                    <li key={f.name}>
                      <Link
                        to={`/prompts/${f.name}`}
                        className={cn(styles.fileItem, f.name === selected && styles.fileItemActive)}
                        aria-current={f.name === selected ? "page" : undefined}
                      >
                        {stripMd(f.name)}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}

        {groups.length > 0 && root.length > 0 && (
          <li><hr className={styles.separator} /></li>
        )}

        {root.map((f) => (
          <li key={f.name}>
            <Link
              to={`/prompts/${f.name}`}
              className={cn(styles.fileItem, f.name === selected && styles.fileItemActive)}
              aria-current={f.name === selected ? "page" : undefined}
            >
              {stripMd(f.name)}
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}
