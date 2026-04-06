import { Space, Select, Input } from "antd";

const { Search } = Input;

const STATUS_OPTIONS = [
  { label: "All Statuses", value: "" },
  { label: "Backlog", value: "backlog" },
  { label: "Scheduled", value: "scheduled" },
  { label: "In Progress", value: "in_progress" },
  { label: "Blocked", value: "blocked" },
  { label: "Waiting Input", value: "waiting_input" },
  { label: "Done", value: "done" },
  { label: "Cancelled", value: "cancelled" },
];

const DOMAIN_OPTIONS = [
  { label: "All Domains", value: "" },
  { label: "Personal", value: "personal" },
  { label: "Work", value: "work" },
  { label: "Family", value: "family" },
];

const PRIORITY_OPTIONS = [
  { label: "All Priorities", value: 0 },
  { label: "P1 — Critical", value: 1 },
  { label: "P2 — High", value: 2 },
  { label: "P3 — Medium", value: 3 },
  { label: "P4 — Low", value: 4 },
  { label: "P5 — Minimal", value: 5 },
];

interface Props {
  status: string;
  domain: string;
  priority: number;
  onStatusChange: (v: string) => void;
  onDomainChange: (v: string) => void;
  onPriorityChange: (v: number) => void;
  onSearch: (v: string) => void;
}

export default function TaskFilters({
  status,
  domain,
  priority,
  onStatusChange,
  onDomainChange,
  onPriorityChange,
  onSearch,
}: Props) {
  return (
    <Space wrap style={{ marginBottom: 12 }}>
      <Select
        size="small"
        value={status}
        onChange={onStatusChange}
        options={STATUS_OPTIONS}
        style={{ width: 150 }}
      />
      <Select
        size="small"
        value={domain}
        onChange={onDomainChange}
        options={DOMAIN_OPTIONS}
        style={{ width: 140 }}
      />
      <Select
        size="small"
        value={priority}
        onChange={onPriorityChange}
        options={PRIORITY_OPTIONS}
        style={{ width: 160 }}
      />
      <Search
        size="small"
        placeholder="Search tasks..."
        allowClear
        onSearch={onSearch}
        style={{ width: 220 }}
      />
    </Space>
  );
}
