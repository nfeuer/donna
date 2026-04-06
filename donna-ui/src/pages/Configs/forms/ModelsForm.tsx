import { Card, Form, Input, InputNumber, Table, Switch } from "antd";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

export default function ModelsForm({ data, onChange }: Props) {
  const models = data.models ?? {};
  const routing = data.routing ?? {};
  const cost = data.cost ?? {};
  const quality = data.quality_monitoring ?? {};

  const updatePath = (path: string[], value: any) => {
    const updated = JSON.parse(JSON.stringify(data));
    let obj = updated;
    for (let i = 0; i < path.length - 1; i++) {
      if (!obj[path[i]]) obj[path[i]] = {};
      obj = obj[path[i]];
    }
    obj[path[path.length - 1]] = value;
    onChange(updated);
  };

  // Model definitions table data
  const modelRows = Object.entries(models).map(([alias, cfg]: [string, any]) => ({
    key: alias,
    alias,
    provider: cfg.provider ?? "",
    model: cfg.model ?? "",
    estimated_cost: cfg.estimated_cost_per_1k_tokens,
  }));

  // Routing table data
  const routingRows = Object.entries(routing).map(([taskType, cfg]: [string, any]) => ({
    key: taskType,
    task_type: taskType,
    model: cfg.model ?? "",
    fallback: cfg.fallback ?? "",
    shadow: cfg.shadow ?? "",
    confidence_threshold: cfg.confidence_threshold,
  }));

  return (
    <div style={{ maxHeight: "calc(100vh - 290px)", overflow: "auto", paddingRight: 8 }}>
      {/* Model Definitions */}
      <Card size="small" title="Model Definitions" style={{ marginBottom: 16 }}>
        <Table
          dataSource={modelRows}
          size="small"
          pagination={false}
          columns={[
            { title: "Alias", dataIndex: "alias", width: 120 },
            {
              title: "Provider",
              dataIndex: "provider",
              width: 120,
              render: (v: string, row) => (
                <Input
                  size="small"
                  value={v}
                  onChange={(e) => updatePath(["models", row.alias, "provider"], e.target.value)}
                />
              ),
            },
            {
              title: "Model",
              dataIndex: "model",
              render: (v: string, row) => (
                <Input
                  size="small"
                  value={v}
                  onChange={(e) => updatePath(["models", row.alias, "model"], e.target.value)}
                />
              ),
            },
          ]}
        />
      </Card>

      {/* Routing Table */}
      <Card size="small" title="Routing Table" style={{ marginBottom: 16 }}>
        <Table
          dataSource={routingRows}
          size="small"
          pagination={false}
          columns={[
            { title: "Task Type", dataIndex: "task_type", width: 160 },
            {
              title: "Model",
              dataIndex: "model",
              width: 120,
              render: (v: string, row) => (
                <Input
                  size="small"
                  value={v}
                  onChange={(e) => updatePath(["routing", row.task_type, "model"], e.target.value)}
                />
              ),
            },
            {
              title: "Fallback",
              dataIndex: "fallback",
              width: 120,
              render: (v: string, row) => (
                <Input
                  size="small"
                  value={v}
                  onChange={(e) => updatePath(["routing", row.task_type, "fallback"], e.target.value || undefined)}
                />
              ),
            },
            {
              title: "Shadow",
              dataIndex: "shadow",
              width: 120,
              render: (v: string, row) => (
                <Input
                  size="small"
                  value={v}
                  onChange={(e) => updatePath(["routing", row.task_type, "shadow"], e.target.value || undefined)}
                />
              ),
            },
            {
              title: "Threshold",
              dataIndex: "confidence_threshold",
              width: 100,
              render: (v: number | undefined, row) => (
                <InputNumber
                  size="small"
                  value={v}
                  min={0}
                  max={1}
                  step={0.1}
                  style={{ width: "100%" }}
                  onChange={(val) => updatePath(["routing", row.task_type, "confidence_threshold"], val ?? undefined)}
                />
              ),
            },
          ]}
        />
      </Card>

      {/* Cost Tracking */}
      <Card size="small" title="Cost Tracking" style={{ marginBottom: 16 }}>
        <Form layout="inline" size="small">
          <Form.Item label="Monthly Budget ($)">
            <InputNumber
              value={cost.monthly_budget_usd}
              min={0}
              step={10}
              onChange={(v) => updatePath(["cost", "monthly_budget_usd"], v)}
            />
          </Form.Item>
          <Form.Item label="Daily Pause ($)">
            <InputNumber
              value={cost.daily_pause_threshold_usd}
              min={0}
              step={5}
              onChange={(v) => updatePath(["cost", "daily_pause_threshold_usd"], v)}
            />
          </Form.Item>
          <Form.Item label="Task Approval ($)">
            <InputNumber
              value={cost.task_approval_threshold_usd}
              min={0}
              step={1}
              onChange={(v) => updatePath(["cost", "task_approval_threshold_usd"], v)}
            />
          </Form.Item>
          <Form.Item label="Warning %">
            <InputNumber
              value={cost.monthly_warning_pct}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) => updatePath(["cost", "monthly_warning_pct"], v)}
            />
          </Form.Item>
        </Form>
      </Card>

      {/* Quality Monitoring */}
      <Card size="small" title="Quality Monitoring">
        <Form layout="inline" size="small">
          <Form.Item label="Enabled">
            <Switch
              checked={quality.enabled}
              onChange={(v) => updatePath(["quality_monitoring", "enabled"], v)}
            />
          </Form.Item>
          <Form.Item label="Spot Check Rate">
            <InputNumber
              value={quality.spot_check_rate}
              min={0}
              max={1}
              step={0.01}
              onChange={(v) => updatePath(["quality_monitoring", "spot_check_rate"], v)}
            />
          </Form.Item>
          <Form.Item label="Flag Threshold">
            <InputNumber
              value={quality.flag_threshold}
              min={0}
              max={1}
              step={0.1}
              onChange={(v) => updatePath(["quality_monitoring", "flag_threshold"], v)}
            />
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
