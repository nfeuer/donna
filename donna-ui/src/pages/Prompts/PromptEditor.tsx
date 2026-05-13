import { useState, useEffect, useCallback } from "react";
import { Save } from "lucide-react";
import Editor from "@monaco-editor/react";
import { toast } from "sonner";
import { Button } from "../../primitives/Button";
import { Pill } from "../../primitives/Pill";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";
import MarkdownPreview from "./MarkdownPreview";
import VariableInspector from "./VariableInspector";
import SaveDiffModal from "../Configs/SaveDiffModal";
import { fetchPrompt, savePrompt, type PromptContent } from "../../api/configs";
import styles from "./Prompts.module.css";

interface Props {
  file: string;
}

export default function PromptEditor({ file }: Props) {
  const filename = decodeURIComponent(file);

  const [meta, setMeta] = useState<PromptContent | null>(null);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"edit" | "preview" | "split">("split");
  const hasChanges = editedContent !== originalContent;

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setShowDiff(false);
    setContentLoading(true);
    fetchPrompt(filename)
      .then((d) => {
        if (cancelled) return;
        setMeta(d);
        setOriginalContent(d.content);
        setEditedContent(d.content);
      })
      .catch(() => {
        if (cancelled) return;
        setMeta(null);
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => { cancelled = true; };
  }, [filename]);

  const handleSave = useCallback(async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await savePrompt(filename, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      toast.success(`Saved ${filename}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [filename, editedContent]);

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
    <>
      <div className={styles.editorHeader}>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div className={styles.editorStatus} role="status" aria-live="polite">
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
          <Button
            variant="primary"
            size="sm"
            disabled={!hasChanges}
            onClick={() => setShowDiff(true)}
          >
            <Save size={14} /> Save
          </Button>
        </div>
      </div>

      {meta && (
        <div className={styles.metaBar}>
          <span>{(meta.size_bytes / 1024).toFixed(1)} KB</span>
          <span>Modified {new Date(meta.modified * 1000).toLocaleDateString()}</span>
          {meta.model_alias && <Pill variant="accent">{meta.model_alias}</Pill>}
          {meta.output_schema && <Pill variant="muted">{meta.output_schema}</Pill>}
        </div>
      )}

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

      <VariableInspector content={editedContent} schemaPath={meta?.output_schema ?? null} />

      <SaveDiffModal
        open={showDiff}
        original={originalContent}
        modified={editedContent}
        filename={filename}
        saving={saving}
        onConfirm={handleSave}
        onCancel={() => setShowDiff(false)}
      />
    </>
  );
}
