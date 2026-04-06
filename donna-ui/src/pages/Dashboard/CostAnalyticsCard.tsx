import { useEffect, useState } from "react";
import { Card, Statistic, Row, Col, Progress, Spin, Tag } from "antd";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  BarChart,
  Bar,
} from "recharts";
import {
  fetchCostAnalytics,
  type CostAnalyticsData,
} from "../../api/dashboard";
import { CHART_COLORS } from "../../theme/darkTheme";

interface Props {
  days: number;
  refreshKey: number;
}

export default function CostAnalyticsCard({ days, refreshKey }: Props) {
  const [data, setData] = useState<CostAnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchCostAnalytics(days)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [days, refreshKey]);

  const s = data?.summary;

  const budgetColor = (pct: number) =>
    pct < 60 ? "#52c41a" : pct < 85 ? "#faad14" : "#ff4d4f";

  const budgetStatus = (pct: number) =>
    pct < 60
      ? ("success" as const)
      : pct < 85
        ? ("normal" as const)
        : ("exception" as const);

  return (
    <Card
      title="Cost Analytics"
      size="small"
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Spin spinning={loading}>
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={5}>
            <Statistic
              title="Today"
              value={s?.today_cost_usd ?? 0}
              prefix="$"
              precision={3}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={5}>
            <Statistic
              title="MTD"
              value={s?.monthly_cost_usd ?? 0}
              prefix="$"
              precision={2}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={5}>
            <Statistic
              title="Projected"
              value={s?.projected_monthly_usd ?? 0}
              prefix="$"
              precision={2}
              valueStyle={{
                fontSize: 22,
                color:
                  (s?.projected_monthly_usd ?? 0) > (s?.monthly_budget_usd ?? 100)
                    ? "#ff4d4f"
                    : undefined,
              }}
            />
          </Col>
          <Col span={9}>
            <div style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}>
              Monthly Budget (${s?.monthly_budget_usd ?? 100})
            </div>
            <Progress
              percent={Math.min(s?.monthly_utilization_pct ?? 0, 100)}
              status={budgetStatus(s?.monthly_utilization_pct ?? 0)}
              strokeColor={budgetColor(s?.monthly_utilization_pct ?? 0)}
              size="small"
              format={(pct) => `${pct?.toFixed(1)}%`}
            />
            <div
              style={{ fontSize: 11, color: "#8c8c8c", marginTop: 2 }}
            >
              ${s?.monthly_remaining_usd?.toFixed(2) ?? "—"} remaining
            </div>
          </Col>
        </Row>

        {data?.time_series && data.time_series.length > 0 && (
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={data.time_series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
              <XAxis
                dataKey="date"
                tick={{ fill: "#8c8c8c", fontSize: 10 }}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis
                tick={{ fill: "#8c8c8c", fontSize: 10 }}
                tickFormatter={(v: number) => `$${v}`}
              />
              <Tooltip
                contentStyle={{
                  background: "#1f1f1f",
                  border: "1px solid #303030",
                }}
                formatter={(value: number) => [`$${value.toFixed(4)}`, "Cost"]}
              />
              <ReferenceLine
                y={s?.daily_budget_usd ?? 20}
                stroke="#ff4d4f"
                strokeDasharray="5 5"
                label={{
                  value: "$20/day",
                  fill: "#ff4d4f",
                  fontSize: 10,
                  position: "right",
                }}
              />
              <Area
                type="monotone"
                dataKey="cost_usd"
                stroke={CHART_COLORS[0]}
                fill={CHART_COLORS[0]}
                fillOpacity={0.2}
                name="Daily Cost"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}

        <Row gutter={16} style={{ marginTop: 12 }}>
          <Col span={12}>
            <div
              style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 8 }}
            >
              By Task Type
            </div>
            {data?.by_task_type && data.by_task_type.length > 0 ? (
              <ResponsiveContainer width="100%" height={120}>
                <BarChart data={data.by_task_type.slice(0, 6)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                  <XAxis
                    type="number"
                    tick={{ fill: "#8c8c8c", fontSize: 10 }}
                    tickFormatter={(v: number) => `$${v}`}
                  />
                  <YAxis
                    type="category"
                    dataKey="task_type"
                    width={100}
                    tick={{ fill: "#8c8c8c", fontSize: 10 }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#1f1f1f",
                      border: "1px solid #303030",
                    }}
                  />
                  <Bar
                    dataKey="cost_usd"
                    fill={CHART_COLORS[0]}
                    name="Cost"
                    radius={[0, 4, 4, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Tag>No data</Tag>
            )}
          </Col>
          <Col span={12}>
            <div
              style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 8 }}
            >
              By Model
            </div>
            {data?.by_model && data.by_model.length > 0 ? (
              <ResponsiveContainer width="100%" height={120}>
                <BarChart data={data.by_model} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                  <XAxis
                    type="number"
                    tick={{ fill: "#8c8c8c", fontSize: 10 }}
                    tickFormatter={(v: number) => `$${v}`}
                  />
                  <YAxis
                    type="category"
                    dataKey="model"
                    width={80}
                    tick={{ fill: "#8c8c8c", fontSize: 10 }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#1f1f1f",
                      border: "1px solid #303030",
                    }}
                  />
                  <Bar
                    dataKey="cost_usd"
                    fill={CHART_COLORS[4]}
                    name="Cost"
                    radius={[0, 4, 4, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Tag>No data</Tag>
            )}
          </Col>
        </Row>
      </Spin>
    </Card>
  );
}
