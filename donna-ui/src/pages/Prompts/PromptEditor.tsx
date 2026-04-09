import { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import Editor from "@monaco-editor/react";
import { toast } from "sonner";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import { Pill } from "../../primitives/Pill";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";
import PromptFileList from "./PromptFileList";
import MarkdownPreview from "./MarkdownPreview";
import VariableInspector from "./VariableInspector";
import SaveDiffModal from "../Configs/SaveDiffModal";
import {
  fetchPrompts,
  fetchPrompt,
  savePrompt,
  fetchConfigs,
  fetchConfig,
  type PromptFile,
} from "../../api/configs";
import styles from "./Prompts.module.css";

function useSchemaMap() {
  const [map, setMap] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const configs = await fetchConfigs();
        if (!configs.some((c) => c.name === "task_types.yaml")) return;
        const data = await fetchConfig("task_types.yaml");
        const next: Record<string, string> = {};
        let currentPrompt = "";
        for (const line of data.content.split("\n")) {
          const p = line.match(/prompt_template:\s*(.+)/);
          const s = line.match(/output_schema:\s*(.+)/);
          if (p) currentPrompt = p[1].trim().split("/").pop() ?? "";
          if (s && currentPrompt) {
            next[currentPrompt] = s[1].trim();
            currentPrompt = "";
          }
        }
        if (!cancelled) setMap(next);
      } catch {
        /* non-critical */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return map;
}

export default function PromptEditor() {
  const { file } = useParams<{ file: string }>();
  const filename = file ? decodeURIComponent(file) : "";

  const [files, setFiles] = useState<PromptFile[]>([]);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"edit" | "preview" | "split">("split");
  const schemaMap = useSchemaMap();
  const hasChanges = editedContent !== originalContent;

  const loadFiles = useCallback(async () => {
    try {
      setFiles(await fetchPrompts());
    } catch {
      setFiles([]);
    }
  }, []);
  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setContentLoading(true);
    fetchPrompt(filename)
      .then((d) => {
        if (cancelled) return;
        setOriginalContent(d.content);
        setEditedContent(d.content);
      })
      .catch(() => {
        if (cancelled) return;
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filename]);

  const handleSave = async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await savePrompt(filename, editedContent);
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

  const linkedSchema = filename ? schemaMap[filename] ?? null : null;

  const editorEl = (
    <Editor
      height="min(60vh, 560px)"
      language="markdown"
      theme={DONNA_MONACO_THEME}
      beforeMount={setupDonnaMonacoTheme}
      value={editedContent}
      onChange={(v) => setEditedContent(v ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
      }}
    />
  );

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Prompts"
        actions={
          <>
            <Link
              to="/prompts"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <ArrowLeft size={14} /> All templates
            </Link>
            <Button
              variant="primary"
              size="sm"
              disabled={!hasChanges}
              onClick={() => setShowDiff(true)}
            >
              <Save size={14} /> Save
            </Button>
          </>
        }
      />

      <div className={styles.editorHeader}>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div>
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
        </div>
      </div>

      <PromptFileList files={files} loading={false} selected={filename} />

      <Tabs value={view} onValueChange={(v) => setView(v as typeof view)}>
        <TabsList>
          <TabsTrigger value="edit">Edit</TabsTrigger>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="split">Split</TabsTrigger>
        </TabsList>
        <TabsContent value="edit">{editorEl}</TabsContent>
        <TabsContent value="preview">
          <MarkdownPreview content={editedContent} />
        </TabsContent>
        <TabsContent value="split">
          <div className={styles.editorGrid}>
            {editorEl}
            <MarkdownPreview content={editedContent} />
          </div>
        </TabsContent>
      </Tabs>

      <VariableInspector content={editedContent} schemaPath={linkedSchema} />

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
