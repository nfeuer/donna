import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { EmptyState } from "../../primitives/EmptyState";
import { fetchPrompts, type PromptFile } from "../../api/configs";
import PromptFileList from "./PromptFileList";
import styles from "./Prompts.module.css";

export default function PromptsList() {
  const [files, setFiles] = useState<PromptFile[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setFiles(await fetchPrompts());
    } catch {
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Prompts"
        meta={
          <span role="status" aria-live="polite">
            {loading
              ? "Loading…"
              : `${files.length} template${files.length === 1 ? "" : "s"}`}
          </span>
        }
      />
      {!loading && files.length === 0 ? (
        <EmptyState
          title="No prompt templates"
          body="Donna reads prompt templates from prompts/. Add one and refresh."
        />
      ) : (
        <PromptFileList files={files} loading={loading} selected={null} />
      )}
    </div>
  );
}
