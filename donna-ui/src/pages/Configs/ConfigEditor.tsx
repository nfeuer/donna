import { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { Pill } from "../../primitives/Pill";
import ConfigFileList from "./ConfigFileList";
import StructuredEditor from "./StructuredEditor";
import RawYamlEditor from "./RawYamlEditor";
import SaveDiffModal from "./SaveDiffModal";
import { useYamlValidator } from "../../hooks/useYamlValidator";
import {
  fetchConfigs,
  fetchConfig,
  saveConfig,
  type ConfigFile,
} from "../../api/configs";
import yaml from "yaml";
import styles from "./Configs.module.css";

export default function ConfigEditor() {
  const { file } = useParams<{ file: string }>();
  const filename = file ? decodeURIComponent(file) : "";

  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("structured");

  const validation = useYamlValidator(editedContent);
  const parsedData = (validation.ok ? validation.data : {}) as Record<string, unknown>;
  const hasChanges = editedContent !== originalContent;

  const loadFiles = useCallback(async () => {
    try { setFiles(await fetchConfigs()); } catch { setFiles([]); }
  }, []);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setContentLoading(true);
    fetchConfig(filename)
      .then((d) => {
        if (cancelled) return;
        setOriginalContent(d.content);
        setEditedContent(d.content);
        setActiveTab("structured");
      })
      .catch(() => {
        if (cancelled) return;
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => { if (!cancelled) setContentLoading(false); });
    return () => { cancelled = true; };
  }, [filename]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleStructuredChange = (data: Record<string, any>) => {
    try { setEditedContent(yaml.stringify(data, { indent: 2 })); } catch { /* keep */ }
  };

  const handleSave = async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await saveConfig(filename, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      toast.success(`Saved ${filename}`);
      loadFiles();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Configs"
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={!hasChanges || !validation.ok}
            onClick={() => setShowDiff(true)}
          >
            <Save size={14} /> Save
          </Button>
        }
      />

      <div className={styles.editorHeader}>
        <Link to="/configs" className={styles.backLink}>
          <ArrowLeft size={14} /> All files
        </Link>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div className={styles.editorSubtitle}>
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
          {!validation.ok && validation.error && (
            <span className={styles.invalid}>
              YAML error: {validation.error.message}
            </span>
          )}
        </div>
      </div>

      <ConfigFileList files={files} loading={false} selected={filename} />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="structured">Structured</TabsTrigger>
          <TabsTrigger value="raw">Raw YAML</TabsTrigger>
        </TabsList>
        <TabsContent value="structured">
          <StructuredEditor
            filename={filename}
            data={parsedData}
            rawYaml={editedContent}
            onDataChange={handleStructuredChange}
            onRawChange={setEditedContent}
          />
        </TabsContent>
        <TabsContent value="raw">
          <RawYamlEditor value={editedContent} onChange={setEditedContent} />
        </TabsContent>
      </Tabs>

      <SaveDiffModal
        open={showDiff}
        original={originalContent}
        modified={editedContent}
        filename={filename}
        saving={saving}
        onConfirm={handleSave}
        onCancel={() => setShowDiff(false)}
      />
    </div>
  );
}
