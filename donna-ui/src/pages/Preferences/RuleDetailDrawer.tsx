import { useState, useEffect } from "react";
import { Drawer, Descriptions, Tag, Progress, Table, Empty, Spin } from "antd";
import type { ColumnsType } from "antd/es/table";
import { fetchCorrections, type PreferenceRule, type CorrectionEntry } from "../../api/preferences";

interface Props {
  rule: PreferenceRule | null;
  open: boolean;
  onClose: () => void;
}

const correctionColumns: ColumnsType<CorrectionEntry> = [
  {
    title: "Timestamp",
    dataIndex: "timestamp",
    key: "timestamp",
    width: 170,
    render: (val: string) => val?.replace("T", " ").substring(0, 19),
  },
  {
    title: "Field",
    dataIndex: "field_corrected",
    key: "field",
    width: 120,
    render: (val: string) => <Tag color="blue">{val}</Tag>,
  },
  {
    title: "Original",
    dataIndex: "original_value",
    key: "original",
    width: 120,
    ellipsis: true,
  },
  {
    title: "Corrected",
    dataIndex: "corrected_value",
    key: "corrected",
    width: 120,
    ellipsis: true,
  },
  {
    title: "Task Type",
    dataIndex: "task_type",
    key: "task_type",
    width: 120,
  },
];

export default function RuleDetailDrawer({ rule, open, onClose }: Props) {
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!rule || !open) return;

    // Fetch all corrections and filter by supporting IDs client-side
    // (the backend doesn't have a "by IDs" endpoint, so we fetch and filter)
    const ids = new Set(rule.supporting_corrections);
    if (ids.size === 0) {
      setCorrections([]);
      return;
    }

    setLoading(true);
    fetchCorrections({ limit: 500 })
      .then((resp) => {
        setCorrections(resp.corrections.filter((c) => ids.has(c.id)));
      })
      .catch(() => setCorrections([]))
      .finally(() => setLoading(false));
  }, [rule, open]);

  return (
    <Drawer
      title="Rule Details"
      width={700}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {rule && (
        <>
          <Descriptions
            column={2}
            size="small"
            bordered
            style={{ marginBottom: 24 }}
          >
            <Descriptions.Item label="Type">
              <Tag>{rule.rule_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Enabled">
              <Tag color={rule.enabled ? "green" : "red"}>
                {rule.enabled ? "Yes" : "No"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Confidence" span={2}>
              <Progress
                percent={Math.round(rule.confidence * 100)}
                size="small"
                status={rule.confidence >= 0.7 ? "normal" : "exception"}
                style={{ width: 200 }}
              />
            </Descriptions.Item>
            <Descriptions.Item label="Rule Text" span={2}>
              {rule.rule_text}
            </Descriptions.Item>
            <Descriptions.Item label="Condition" span={2}>
              <pre style={{ margin: 0, fontSize: 11 }}>
                {rule.condition ? JSON.stringify(rule.condition, null, 2) : "any"}
              </pre>
            </Descriptions.Item>
            <Descriptions.Item label="Action" span={2}>
              <pre style={{ margin: 0, fontSize: 11 }}>
                {rule.action ? JSON.stringify(rule.action, null, 2) : "-"}
              </pre>
            </Descriptions.Item>
            <Descriptions.Item label="Created">
              {rule.created_at?.substring(0, 10)}
            </Descriptions.Item>
            <Descriptions.Item label="Disabled At">
              {rule.disabled_at?.substring(0, 10) ?? "-"}
            </Descriptions.Item>
          </Descriptions>

          <h4 style={{ marginBottom: 12 }}>
            Supporting Corrections ({rule.supporting_corrections.length})
          </h4>
          <Spin spinning={loading}>
            <Table<CorrectionEntry>
              columns={correctionColumns}
              dataSource={corrections}
              rowKey="id"
              size="small"
              pagination={false}
              locale={{
                emptyText: (
                  <Empty description="No supporting corrections found" />
                ),
              }}
            />
          </Spin>
        </>
      )}
    </Drawer>
  );
}
