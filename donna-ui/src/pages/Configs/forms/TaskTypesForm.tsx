import { Collapse, Form, Input, Select, Tag } from "antd";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const MODEL_OPTIONS = [
  { label: "parser", value: "parser" },
  { label: "reasoner", value: "reasoner" },
  { label: "fallback", value: "fallback" },
  { label: "local_parser", value: "local_parser" },
];

export default function TaskTypesForm({ data, onChange }: Props) {
  const taskTypes = data.task_types ?? {};

  const updateField = (ttName: string, field: string, value: any) => {
    const updated = {
      ...data,
      task_types: {
        ...taskTypes,
        [ttName]: {
          ...taskTypes[ttName],
          [field]: value,
        },
      },
    };
    onChange(updated);
  };

  const items = Object.entries(taskTypes).map(([name, cfg]: [string, any]) => ({
    key: name,
    label: (
      <span>
        <strong>{name}</strong>
        <Tag color="blue" style={{ marginLeft: 8 }}>{cfg.model}</Tag>
      </span>
    ),
    children: (
      <Form layout="vertical" size="small">
        <Form.Item label="Description">
          <Input
            value={cfg.description}
            onChange={(e) => updateField(name, "description", e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Model">
          <Select
            value={cfg.model}
            options={MODEL_OPTIONS}
            onChange={(v) => updateField(name, "model", v)}
          />
        </Form.Item>
        <Form.Item label="Shadow Model">
          <Select
            value={cfg.shadow}
            options={[{ label: "(none)", value: "" }, ...MODEL_OPTIONS]}
            onChange={(v) => updateField(name, "shadow", v || undefined)}
            allowClear
          />
        </Form.Item>
        <Form.Item label="Prompt Template">
          <Input
            value={cfg.prompt_template}
            onChange={(e) => updateField(name, "prompt_template", e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Output Schema">
          <Input
            value={cfg.output_schema}
            onChange={(e) => updateField(name, "output_schema", e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Tools">
          <Select
            mode="tags"
            value={cfg.tools ?? []}
            onChange={(v) => updateField(name, "tools", v)}
            tagRender={({ label, closable, onClose }) => (
              <Tag closable={closable} onClose={onClose} style={{ marginRight: 4 }}>
                {label}
              </Tag>
            )}
          />
        </Form.Item>
      </Form>
    ),
  }));

  return (
    <div style={{ maxHeight: "calc(100vh - 290px)", overflow: "auto", paddingRight: 8 }}>
      <Collapse items={items} />
    </div>
  );
}
