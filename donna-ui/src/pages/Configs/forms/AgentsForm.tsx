import { Card, Form, Switch, InputNumber, Select, Tag, Row, Col } from "antd";

interface AgentConfig {
  enabled: boolean;
  timeout_seconds: number;
  autonomy: string;
  allowed_tools: string[];
}

interface Props {
  data: Record<string, Record<string, AgentConfig>>;
  onChange: (data: Record<string, Record<string, AgentConfig>>) => void;
}

const AUTONOMY_OPTIONS = [
  { label: "Low", value: "low" },
  { label: "Medium", value: "medium" },
  { label: "High", value: "high" },
];

const ALL_TOOLS = [
  "task_db_read", "task_db_write", "calendar_read", "calendar_write",
  "web_search", "email_read", "email_draft", "notes_read",
  "fs_read", "fs_write", "github_read", "github_write",
  "docs_write", "discord_write", "cost_summary",
];

export default function AgentsForm({ data, onChange }: Props) {
  const agents = data.agents ?? {};

  const updateAgent = (name: string, field: string, value: unknown) => {
    const updated = {
      ...data,
      agents: {
        ...agents,
        [name]: {
          ...agents[name],
          [field]: value,
        },
      },
    };
    onChange(updated);
  };

  return (
    <div style={{ maxHeight: "calc(100vh - 290px)", overflow: "auto", paddingRight: 8 }}>
      <Row gutter={[16, 16]}>
        {Object.entries(agents).map(([name, cfg]) => (
          <Col xs={24} lg={12} key={name}>
            <Card
              size="small"
              title={
                <span style={{ textTransform: "capitalize", fontWeight: 600 }}>
                  {name}
                </span>
              }
              extra={
                <Switch
                  checked={cfg.enabled}
                  checkedChildren="On"
                  unCheckedChildren="Off"
                  onChange={(v) => updateAgent(name, "enabled", v)}
                />
              }
            >
              <Form layout="vertical" size="small">
                <Form.Item label="Timeout (seconds)">
                  <InputNumber
                    value={cfg.timeout_seconds}
                    min={10}
                    max={3600}
                    style={{ width: "100%" }}
                    onChange={(v) => updateAgent(name, "timeout_seconds", v ?? 60)}
                  />
                </Form.Item>
                <Form.Item label="Autonomy Level">
                  <Select
                    value={cfg.autonomy}
                    options={AUTONOMY_OPTIONS}
                    onChange={(v) => updateAgent(name, "autonomy", v)}
                  />
                </Form.Item>
                <Form.Item label="Allowed Tools">
                  <Select
                    mode="multiple"
                    value={cfg.allowed_tools}
                    options={ALL_TOOLS.map((t) => ({ label: t, value: t }))}
                    onChange={(v) => updateAgent(name, "allowed_tools", v)}
                    tagRender={({ label, closable, onClose }) => (
                      <Tag closable={closable} onClose={onClose} style={{ marginRight: 4 }}>
                        {label}
                      </Tag>
                    )}
                  />
                </Form.Item>
              </Form>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
