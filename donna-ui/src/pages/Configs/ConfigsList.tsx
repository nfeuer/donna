import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { EmptyState } from "../../primitives/EmptyState";
import { fetchConfigs, type ConfigFile } from "../../api/configs";
import ConfigFileList from "./ConfigFileList";
import styles from "./Configs.module.css";

export default function ConfigsList() {
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setFiles(await fetchConfigs());
    } catch {
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Configs"
        meta={loading ? "Loading…" : `${files.length} file${files.length === 1 ? "" : "s"}`}
      />
      {!loading && files.length === 0 ? (
        <EmptyState
          title="No config files"
          body="Donna reads YAML from the config/ directory. Add one and refresh."
        />
      ) : (
        <ConfigFileList files={files} loading={loading} selected={null} />
      )}
    </div>
  );
}
