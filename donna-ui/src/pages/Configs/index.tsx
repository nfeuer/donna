import { useState, useEffect, useCallback, useRef } from "react";
import { Layout, Tabs, Button, Space, notification, Badge } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import yaml from "yaml";
import ConfigFileList from "./ConfigFileList";
import RawYamlEditor from "./RawYamlEditor";
import StructuredEditor from "./StructuredEditor";
import SaveDiffModal from "./SaveDiffModal";
import {
  fetchConfigs,
  fetchConfig,
  saveConfig,
  type ConfigFile,
} from "../../api/configs";

const { Sider, Content } = Layout;

export default function ConfigsPage() {
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [parsedData, setParsedData] = useState<Record<string, any>>({});
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("structured");
  const structuredDirty = useRef(false);

  const hasChanges = editedContent !== originalContent;

  const loadFiles = useCallback(async () => {
    setFilesLoading(true);
    try {
      const data = await fetchConfigs();
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
      const data = await fetchConfig(name);
      setOriginalContent(data.content);
      setEditedContent(data.content);
      try {
        setParsedData(yaml.parse(data.content) ?? {});
      } catch {
        setParsedData({});
      }
      structuredDirty.current = false;
    } catch {
      setOriginalContent("");
      setEditedContent("");
      setParsedData({});
    } finally {
      setContentLoading(false);
    }
  }, []);

  const handleFileSelect = (name: string) => {
    setSelected(name);
    setActiveTab("structured");
    loadContent(name);
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleStructuredChange = (data: Record<string, any>) => {
    setParsedData(data);
    structuredDirty.current = true;
    // Serialize back to YAML
    try {
      const newYaml = yaml.stringify(data, { indent: 2 });
      setEditedContent(newYaml);
    } catch {
      // Keep current edited content if serialization fails
    }
  };

  const handleRawChange = (value: string) => {
    setEditedContent(value);
    // Try to parse and update structured view
    try {
      setParsedData(yaml.parse(value) ?? {});
    } catch {
      // Invalid YAML — keep current parsed data
    }
  };

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await saveConfig(selected, editedContent);
      setOriginalContent(editedContent);
      structuredDirty.current = false;
      setShowDiff(false);
      notification.success({ message: "Config saved", description: `${selected} updated.` });
      loadFiles(); // Refresh metadata
    } catch (err) {
      notification.error({
        message: "Save failed",
        description: err instanceof Error ? err.message : "Unknown error",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout style={{ background: "transparent", minHeight: "calc(100vh - 130px)" }}>
      <Sider
        width={220}
        style={{
          background: "#1f1f1f",
          borderRadius: 6,
          padding: 12,
          marginRight: 16,
          overflow: "auto",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>
          Config Files
        </div>
        <ConfigFileList
          files={files}
          loading={filesLoading}
          selected={selected}
          onSelect={handleFileSelect}
        />
      </Sider>

      <Content>
        {!selected ? (
          <div style={{ padding: 40, textAlign: "center", color: "#666" }}>
            Select a config file from the sidebar to edit.
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
                {hasChanges && (
                  <Badge status="warning" text="Unsaved changes" />
                )}
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

            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              size="small"
              items={[
                {
                  key: "structured",
                  label: "Structured",
                  children: (
                    <StructuredEditor
                      filename={selected}
                      data={parsedData}
                      rawYaml={editedContent}
                      onDataChange={handleStructuredChange}
                      onRawChange={handleRawChange}
                    />
                  ),
                },
                {
                  key: "raw",
                  label: "Raw YAML",
                  children: (
                    <RawYamlEditor
                      value={editedContent}
                      onChange={handleRawChange}
                    />
                  ),
                },
              ]}
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
