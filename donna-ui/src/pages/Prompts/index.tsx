import { useState, useEffect, useCallback } from "react";
import { Layout, Button, Space, notification, Badge, Row, Col } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import Editor from "@monaco-editor/react";
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

const { Sider, Content } = Layout;

export default function PromptsPage() {
  const [files, setFiles] = useState<PromptFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [schemaMap, setSchemaMap] = useState<Record<string, string>>({});

  const hasChanges = editedContent !== originalContent;

  // Load prompt-to-schema mapping from task_types.yaml
  useEffect(() => {
    (async () => {
      try {
        const configs = await fetchConfigs();
        const hasTT = configs.some((c) => c.name === "task_types.yaml");
        if (!hasTT) return;
        const ttData = await fetchConfig("task_types.yaml");
        // Simple YAML parsing for prompt_template → output_schema mapping
        const map: Record<string, string> = {};
        const lines = ttData.content.split("\n");
        let currentPrompt = "";
        for (const line of lines) {
          const promptMatch = line.match(/prompt_template:\s*(.+)/);
          const schemaMatch = line.match(/output_schema:\s*(.+)/);
          if (promptMatch) {
            currentPrompt = promptMatch[1].trim().split("/").pop() ?? "";
          }
          if (schemaMatch && currentPrompt) {
            map[currentPrompt] = schemaMatch[1].trim();
            currentPrompt = "";
          }
        }
        setSchemaMap(map);
      } catch {
        // Non-critical
      }
    })();
  }, []);

  const loadFiles = useCallback(async () => {
    setFilesLoading(true);
    try {
      const data = await fetchPrompts();
      setFiles(data);
    } catch {
      setFiles([]);
    } finally {
      setFilesLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const loadContent = useCallback(async (name: string) => {
    setContentLoading(true);
    try {
      const data = await fetchPrompt(name);
      setOriginalContent(data.content);
      setEditedContent(data.content);
    } catch {
      setOriginalContent("");
      setEditedContent("");
    } finally {
      setContentLoading(false);
    }
  }, []);

  const handleFileSelect = (name: string) => {
    setSelected(name);
    loadContent(name);
  };

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await savePrompt(selected, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      notification.success({ message: "Prompt saved", description: `${selected} updated.` });
      loadFiles();
    } catch (err) {
      notification.error({
        message: "Save failed",
        description: err instanceof Error ? err.message : "Unknown error",
      });
    } finally {
      setSaving(false);
    }
  };

  const linkedSchema = selected ? schemaMap[selected] ?? null : null;

  return (
    <Layout style={{ background: "transparent", minHeight: "calc(100vh - 130px)" }}>
      <Sider
        width={210}
        style={{
          background: "#1f1f1f",
          borderRadius: 6,
          padding: 12,
          marginRight: 16,
          overflow: "auto",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>
          Prompt Templates
        </div>
        <PromptFileList
          files={files}
          loading={filesLoading}
          selected={selected}
          onSelect={handleFileSelect}
        />
      </Sider>

      <Content>
        {!selected ? (
          <div style={{ padding: 40, textAlign: "center", color: "#666" }}>
            Select a prompt template to edit.
          </div>
        ) : contentLoading ? (
          <div style={{ padding: 40, textAlign: "center", color: "#666" }}>
            Loading...
          </div>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              }}
            >
              <span style={{ fontWeight: 600 }}>{selected}</span>
              <Space>
                {hasChanges && <Badge status="warning" text="Unsaved changes" />}
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  disabled={!hasChanges}
                  onClick={() => setShowDiff(true)}
                  size="small"
                >
                  Save
                </Button>
              </Space>
            </div>

            <Row gutter={12}>
              {/* Editor */}
              <Col span={12}>
                <Editor
                  height="calc(100vh - 320px)"
                  language="markdown"
                  theme="vs-dark"
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
              </Col>

              {/* Preview */}
              <Col span={12}>
                <MarkdownPreview content={editedContent} />
              </Col>
            </Row>

            {/* Variable Inspector */}
            <VariableInspector
              content={editedContent}
              schemaPath={linkedSchema}
            />

            <SaveDiffModal
              open={showDiff}
              original={originalContent}
              modified={editedContent}
              filename={selected}
              saving={saving}
              onConfirm={handleSave}
              onCancel={() => setShowDiff(false)}
            />
          </>
        )}
      </Content>
    </Layout>
  );
}
