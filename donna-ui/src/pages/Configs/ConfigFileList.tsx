import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Skeleton } from "../../primitives/Skeleton";
import dayjs from "dayjs";
import type { ConfigFile } from "../../api/configs";
import styles from "./Configs.module.css";

interface Props {
  files: ConfigFile[];
  loading: boolean;
  selected: string | null;
}

export default function ConfigFileList({ files, loading, selected }: Props) {
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
    <nav className={styles.list} aria-label="Config files">
      {files.map((f) => {
        const active = f.name === selected;
        return (
          <Link
            key={f.name}
            to={`/configs/${encodeURIComponent(f.name)}`}
            className={cn(styles.item, active && styles.itemActive)}
            aria-current={active ? "page" : undefined}
          >
            <span>{f.name}</span>
            <span className={styles.meta}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
