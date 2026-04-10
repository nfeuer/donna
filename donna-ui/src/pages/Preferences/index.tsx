import { useState, useCallback, useEffect } from "react";
import { Card, Row, Col, Statistic, Select, Tabs, Space, Tag } from "antd";
import {
  BulbOutlined,
  CheckCircleOutlined,
  StopOutlined,
  EditOutlined,
} from "@ant-design/icons";
import RefreshButton from "../../components/RefreshButton";
import RulesTable from "./RulesTable";
import CorrectionsTable from "./CorrectionsTable";
import RuleDetailDrawer from "./RuleDetailDrawer";
import {
  fetchPreferenceRules,
  fetchCorrections,
  fetchPreferenceStats,
  type PreferenceRule,
  type CorrectionEntry,
  type PreferenceStats,
} from "../../api/preferences";
import { STATUS_COLORS } from "../../theme/darkTheme";

export default function PreferencesPage() {
  // Filters
  const [ruleType, setRuleType] = useState("");
  const [enabledFilter, setEnabledFilter] = useState<string>("");
  const [corrField, setCorrField] = useState("");
  const [corrTaskType, setCorrTaskType] = useState("");

  // Data
  const [rules, setRules] = useState<PreferenceRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [corrTotal, setCorrTotal] = useState(0);
  const [corrLoading, setCorrLoading] = useState(false);
  const [corrPage, setCorrPage] = useState(1);
  const corrPageSize = 50;
  const [stats, setStats] = useState<PreferenceStats | null>(null);

  // Drawer
  const [selectedRule, setSelectedRule] = useState<PreferenceRule | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setRulesLoading(true);
    setCorrLoading(true);
    try {
      const enabledVal =
        enabledFilter === "true" ? true : enabledFilter === "false" ? false : undefined;

      const [rulesResp, corrResp, statsResp] = await Promise.all([
        fetchPreferenceRules({
          rule_type: ruleType || undefined,
          enabled: enabledVal,
        }),
        fetchCorrections({
          field: corrField || undefined,
          task_type: corrTaskType || undefined,
          limit: corrPageSize,
          offset: (corrPage - 1) * corrPageSize,
        }),
        fetchPreferenceStats(),
      ]);
      setRules(rulesResp.rules);
      setCorrections(corrResp.corrections);
      setCorrTotal(corrResp.total);
      setStats(statsResp);
    } catch {
      setRules([]);
      setCorrections([]);
      setCorrTotal(0);
      setStats(null);
    } finally {
      setRulesLoading(false);
      setCorrLoading(false);
    }
  }, [ruleType, enabledFilter, corrField, corrTaskType, corrPage, corrPageSize]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  // Listen for keyboard close-drawer event
  useEffect(() => {
    const handler = () => setDrawerOpen(false);
    window.addEventListener("close-drawer", handler);
    return () => window.removeEventListener("close-drawer", handler);
  }, []);

  const handleRuleClick = (rule: PreferenceRule) => {
    setSelectedRule(rule);
    setDrawerOpen(true);
  };

  return (
    <div>
      {/* Controls */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Space>
          <Select
            size="small"
            value={ruleType || undefined}
            placeholder="All rule types"
            allowClear
            onChange={(v) => setRuleType(v ?? "")}
            style={{ width: 150 }}
            options={[
              { value: "scheduling", label: "Scheduling" },
              { value: "priority", label: "Priority" },
              { value: "domain", label: "Domain" },
              { value: "formatting", label: "Formatting" },
              { value: "delegation", label: "Delegation" },
            ]}
          />
          <Select
            size="small"
            value={enabledFilter || undefined}
            placeholder="All states"
            allowClear
            onChange={(v) => setEnabledFilter(v ?? "")}
            style={{ width: 120 }}
            options={[
              { value: "true", label: "Enabled" },
              { value: "false", label: "Disabled" },
            ]}
          />
        </Space>
        <RefreshButton onRefresh={doFetch} />
      </div>

      {/* Stats cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Total Rules"
              value={stats?.total_rules ?? 0}
              prefix={<BulbOutlined />}
              valueStyle={{ color: STATUS_COLORS.INFO }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Active Rules"
              value={stats?.active_rules ?? 0}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: STATUS_COLORS.SUCCESS }}
              suffix={
                stats?.disabled_rules ? (
                  <Tag color="red" style={{ marginLeft: 4, fontSize: 11 }}>
                    {stats.disabled_rules} disabled
                  </Tag>
                ) : null
              }
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Total Corrections"
              value={stats?.total_corrections ?? 0}
              prefix={<EditOutlined />}
              valueStyle={{ color: STATUS_COLORS.WARNING }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Avg Confidence"
              value={stats?.avg_confidence != null ? `${Math.round(stats.avg_confidence * 100)}%` : "N/A"}
              prefix={<StopOutlined />}
              valueStyle={{ color: STATUS_COLORS.INFO }}
              suffix={
                stats?.top_fields?.length ? (
                  <span style={{ fontSize: 11, color: "#8c8c8c", marginLeft: 4 }}>
                    Top: {stats.top_fields[0].field}
                  </span>
                ) : null
              }
            />
          </Card>
        </Col>
      </Row>

      {/* Tabs */}
      <Tabs
        defaultActiveKey="rules"
        items={[
          {
            key: "rules",
            label: `Rules (${rules.length})`,
            children: (
              <Card size="small">
                <RulesTable
                  rules={rules}
                  loading={rulesLoading}
                  onRuleClick={handleRuleClick}
                  onRuleToggled={doFetch}
                />
              </Card>
            ),
          },
          {
            key: "corrections",
            label: `Corrections (${corrTotal})`,
            children: (
              <Card size="small">
                <div style={{ marginBottom: 12 }}>
                  <Space>
                    <Select
                      size="small"
                      value={corrField || undefined}
                      placeholder="All fields"
                      allowClear
                      onChange={(v) => { setCorrField(v ?? ""); setCorrPage(1); }}
                      style={{ width: 150 }}
                      options={
                        stats?.top_fields?.map((f) => ({
                          value: f.field,
                          label: `${f.field} (${f.count})`,
                        })) ?? []
                      }
                    />
                    <Select
                      size="small"
                      value={corrTaskType || undefined}
                      placeholder="All task types"
                      allowClear
                      onChange={(v) => { setCorrTaskType(v ?? ""); setCorrPage(1); }}
                      style={{ width: 160 }}
                      options={[
                        { value: "parse_task", label: "parse_task" },
                        { value: "classify_priority", label: "classify_priority" },
                        { value: "extract_deadline", label: "extract_deadline" },
                      ]}
                    />
                  </Space>
                </div>
                <CorrectionsTable
                  corrections={corrections}
                  loading={corrLoading}
                />
              </Card>
            ),
          },
        ]}
      />

      {/* Rule detail drawer */}
      <RuleDetailDrawer
        rule={selectedRule}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
