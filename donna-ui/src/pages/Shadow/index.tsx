import { useState, useCallback, useEffect } from "react";
import { Card, Row, Col, Statistic, Select, Tabs, Space, Tag } from "antd";
import {
  ExperimentOutlined,
  TrophyOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
} from "@ant-design/icons";
import RefreshButton from "../../components/RefreshButton";
import ComparisonTable from "./ComparisonTable";
import SpotCheckTable from "./SpotCheckTable";
import ShadowCharts from "./ShadowCharts";
import {
  fetchShadowComparisons,
  fetchShadowStats,
  fetchSpotChecks,
  type ShadowComparison,
  type ShadowStats,
  type SpotCheckItem,
} from "../../api/shadow";
import { STATUS_COLORS } from "../../theme/darkTheme";

export default function ShadowPage() {
  const [taskType, setTaskType] = useState("");
  const [days, setDays] = useState(30);

  // Comparisons
  const [comparisons, setComparisons] = useState<ShadowComparison[]>([]);
  const [compLoading, setCompLoading] = useState(false);

  // Stats
  const [stats, setStats] = useState<ShadowStats | null>(null);

  // Spot checks
  const [spotChecks, setSpotChecks] = useState<SpotCheckItem[]>([]);
  const [spotTotal, setSpotTotal] = useState(0);
  const [spotLoading, setSpotLoading] = useState(false);
  const [spotPage, setSpotPage] = useState(1);
  const [spotPageSize, setSpotPageSize] = useState(50);

  const doFetch = useCallback(async () => {
    setCompLoading(true);
    setSpotLoading(true);
    try {
      const [compResp, statsResp, spotResp] = await Promise.all([
        fetchShadowComparisons({
          task_type: taskType || undefined,
          days,
          limit: 50,
        }),
        fetchShadowStats(days),
        fetchSpotChecks(spotPageSize, (spotPage - 1) * spotPageSize),
      ]);
      setComparisons(compResp.comparisons);
      setStats(statsResp);
      setSpotChecks(spotResp.items);
      setSpotTotal(spotResp.total);
    } catch {
      setComparisons([]);
      setStats(null);
      setSpotChecks([]);
      setSpotTotal(0);
    } finally {
      setCompLoading(false);
      setSpotLoading(false);
    }
  }, [taskType, days, spotPage, spotPageSize]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleSpotPageChange = (page: number, size: number) => {
    setSpotPage(page);
    setSpotPageSize(size);
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
            value={taskType || undefined}
            placeholder="All task types"
            allowClear
            onChange={(v) => { setTaskType(v ?? ""); setSpotPage(1); }}
            style={{ width: 180 }}
            options={[
              { value: "parse_task", label: "parse_task" },
              { value: "classify_priority", label: "classify_priority" },
              { value: "extract_deadline", label: "extract_deadline" },
              { value: "generate_nudge", label: "generate_nudge" },
              { value: "prep_work", label: "prep_work" },
            ]}
          />
          <Select
            size="small"
            value={days}
            onChange={(v) => setDays(v)}
            style={{ width: 100 }}
            options={[
              { value: 7, label: "7 days" },
              { value: 14, label: "14 days" },
              { value: 30, label: "30 days" },
              { value: 90, label: "90 days" },
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
              title="Avg Quality Delta"
              value={stats?.avg_delta ?? "N/A"}
              precision={4}
              prefix={<ExperimentOutlined />}
              valueStyle={{
                color: stats?.avg_delta
                  ? stats.avg_delta > 0
                    ? STATUS_COLORS.SUCCESS
                    : stats.avg_delta < 0
                      ? STATUS_COLORS.ERROR
                      : STATUS_COLORS.WARNING
                  : "#8c8c8c",
              }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Shadow Wins"
              value={stats?.wins ?? 0}
              prefix={<TrophyOutlined />}
              valueStyle={{ color: STATUS_COLORS.SUCCESS }}
              suffix={
                <Tag color="green" style={{ marginLeft: 4, fontSize: 11 }}>
                  shadow better
                </Tag>
              }
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Shadow Losses"
              value={stats?.losses ?? 0}
              prefix={<CloseCircleOutlined />}
              valueStyle={{ color: STATUS_COLORS.ERROR }}
              suffix={
                <Tag color="red" style={{ marginLeft: 4, fontSize: 11 }}>
                  primary better
                </Tag>
              }
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Ties"
              value={stats?.ties ?? 0}
              prefix={<MinusCircleOutlined />}
              valueStyle={{ color: STATUS_COLORS.WARNING }}
              suffix={
                <span style={{ fontSize: 11, color: "#8c8c8c", marginLeft: 4 }}>
                  Cost: ${stats ? (stats.primary_cost - stats.shadow_cost).toFixed(2) : "0"} saved
                </span>
              }
            />
          </Card>
        </Col>
      </Row>

      {/* Tabs */}
      <Tabs
        defaultActiveKey="comparisons"
        items={[
          {
            key: "comparisons",
            label: `Comparisons (${comparisons.length})`,
            children: (
              <Card size="small">
                <ComparisonTable comparisons={comparisons} loading={compLoading} />
              </Card>
            ),
          },
          {
            key: "spot-checks",
            label: `Spot Checks (${spotTotal})`,
            children: (
              <Card size="small">
                <SpotCheckTable
                  items={spotChecks}
                  total={spotTotal}
                  loading={spotLoading}
                  page={spotPage}
                  pageSize={spotPageSize}
                  onPageChange={handleSpotPageChange}
                />
              </Card>
            ),
          },
          {
            key: "charts",
            label: "Charts",
            children: <ShadowCharts comparisons={comparisons} stats={stats} loading={compLoading} />,
          },
        ]}
      />
    </div>
  );
}
