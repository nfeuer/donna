import { Link } from "react-router-dom";
import dayjs from "dayjs";
import { cn } from "../../lib/cn";
import { Skeleton } from "../../primitives/Skeleton";
import type { PromptFile } from "../../api/configs";
import styles from "./Prompts.module.css";

interface Props {
  files: PromptFile[];
  loading: boolean;
  selected: string | null;
}

export default function PromptFileList({ files, loading, selected }: Props) {
  if (loading) {
    return (
      <div className={styles.list}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={36} />
        ))}
      </div>
    );
  }

  return (
    <nav className={styles.list} aria-label="Prompt templates">
      {files.map((f) => {
        const active = f.name === selected;
        return (
          <Link
            key={f.name}
            to={`/prompts/${encodeURIComponent(f.name)}`}
            className={cn(styles.item, active && styles.itemActive)}
            aria-current={active ? "page" : undefined}
          >
            <span>{f.name.replace(".md", "")}</span>
            <span className={styles.meta}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
