import { useState } from "react";
import { Table, Tag, Progress, Switch, Empty, notification } from "antd";
import type { ColumnsType } from "antd/es/table";
import { toggleRule, type PreferenceRule } from "../../api/preferences";

interface Props {
  rules: PreferenceRule[];
  loading: boolean;
  onRuleClick: (rule: PreferenceRule) => void;
  onRuleToggled: () => void;
}

const RULE_TYPE_COLORS: Record<string, string> = {
  scheduling: "blue",
  priority: "orange",
  domain: "green",
  formatting: "purple",
  delegation: "cyan",
};

export default function RulesTable({ rules, loading, onRuleClick, onRuleToggled }: Props) {
  const [toggling, setToggling] = useState<string | null>(null);

  const handleToggle = async (rule: PreferenceRule, checked: boolean) => {
    setToggling(rule.id);
    try {
      await toggleRule(rule.id, checked);
      onRuleToggled();
    } catch {
      notification.error({
        message: "Toggle Failed",
        description: `Could not ${checked ? "enable" : "disable"} rule.`,
      });
    } finally {
      setToggling(null);
    }
  };

  const columns: ColumnsType<PreferenceRule> = [
    {
      title: "Type",
      dataIndex: "rule_type",
      key: "rule_type",
      width: 110,
      render: (val: string) => (
        <Tag color={RULE_TYPE_COLORS[val] ?? "default"}>{val}</Tag>
      ),
    },
    {
      title: "Rule",
      dataIndex: "rule_text",
      key: "rule_text",
      ellipsis: true,
    },
    {
      title: "Confidence",
      dataIndex: "confidence",
      key: "confidence",
      width: 150,
      render: (val: number) => (
        <Progress
          percent={Math.round(val * 100)}
          size="small"
          status={val >= 0.7 ? "normal" : "exception"}
          format={(pct) => `${pct}%`}
        />
      ),
      sorter: (a, b) => a.confidence - b.confidence,
    },
    {
      title: "Condition",
      dataIndex: "condition",
      key: "condition",
      width: 140,
      ellipsis: true,
      render: (val: Record<string, unknown> | null) =>
        val ? JSON.stringify(val) : <span style={{ color: "#8c8c8c" }}>any</span>,
    },
    {
      title: "Action",
      dataIndex: "action",
      key: "action",
      width: 140,
      ellipsis: true,
      render: (val: Record<string, unknown> | null) =>
        val ? JSON.stringify(val) : <span style={{ color: "#8c8c8c" }}>-</span>,
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      width: 80,
      render: (_: boolean, record: PreferenceRule) => (
        <Switch
          checked={record.enabled}
          loading={toggling === record.id}
          onChange={(checked) => handleToggle(record, checked)}
          size="small"
          onClick={(_, e) => e.stopPropagation()}
        />
      ),
    },
    {
      title: "Corrections",
      key: "corrections",
      width: 100,
      render: (_: unknown, record: PreferenceRule) => (
        <span>{record.supporting_corrections.length}</span>
      ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 120,
      render: (val: string) => val?.substring(0, 10),
    },
  ];

  return (
    <Table<PreferenceRule>
      columns={columns}
      dataSource={rules}
      rowKey="id"
      loading={loading}
      size="small"
      onRow={(record) => ({
        onClick: () => onRuleClick(record),
        style: { cursor: "pointer" },
      })}
      pagination={{
        pageSize: 20,
        showSizeChanger: true,
        pageSizeOptions: ["10", "20", "50"],
      }}
      locale={{ emptyText: <Empty description="No learned preference rules yet" /> }}
    />
  );
}
