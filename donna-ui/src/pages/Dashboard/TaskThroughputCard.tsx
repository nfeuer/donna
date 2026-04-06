import { useEffect, useState } from "react";
import { Card, Statistic, Row, Col, Spin } from "antd";
import {
  ComposedChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import {
  fetchTaskThroughput,
  type TaskThroughputData,
} from "../../api/dashboard";
import { CHART_COLORS, STATUS_COLORS } from "../../theme/darkTheme";

interface Props {
  days: number;
  refreshKey: number;
}

const STATUS_PIE_COLORS: Record<string, string> = {
  backlog: "#8c8c8c",
  scheduled: "#1890ff",
  in_progress: "#faad14",
  blocked: "#ff4d4f",
  waiting_input: "#722ed1",
  done: "#52c41a",
  cancelled: "#434343",
};

export default function TaskThroughputCard({ days, refreshKey }: Props) {
  const [data, setData] = useState<TaskThroughputData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchTaskThroughput(days)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [days, refreshKey]);

  const s = data?.summary;

  const pieData = data?.status_distribution
    ? Object.entries(data.status_distribution).map(([name, value]) => ({
        name,
        value,
      }))
    : [];

  return (
    <Card
      title="Task Throughput"
      size="small"
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Spin spinning={loading}>
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={4}>
            <Statistic
              title="Created"
              value={s?.total_created ?? 0}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={4}>
            <Statistic
              title="Completed"
              value={s?.total_completed ?? 0}
              valueStyle={{ fontSize: 22, color: STATUS_COLORS.SUCCESS }}
            />
          </Col>
          <Col span={5}>
            <Statistic
              title="Completion"
              value={s?.completion_rate ?? 0}
              suffix="%"
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={5}>
            <Statistic
              title="Avg Hours"
              value={s?.avg_completion_hours ?? "—"}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={3}>
            <Statistic
              title="Overdue"
              value={s?.overdue_count ?? 0}
              valueStyle={{
                fontSize: 22,
                color:
                  (s?.overdue_count ?? 0) > 0
                    ? STATUS_COLORS.ERROR
                    : undefined,
              }}
            />
          </Col>
          <Col span={3}>
            <Statistic
              title="Reschedules"
              value={s?.avg_reschedules ?? 0}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={16}>
            {data?.time_series && data.time_series.length > 0 && (
              <ResponsiveContainer width="100%" height={180}>
                <ComposedChart data={data.time_series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: "#8c8c8c", fontSize: 10 }}
                    tickFormatter={(v: string) => v.slice(5)}
                  />
                  <YAxis tick={{ fill: "#8c8c8c", fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{
                      background: "#1f1f1f",
                      border: "1px solid #303030",
                    }}
                  />
                  <Bar
                    dataKey="created"
                    fill={CHART_COLORS[0]}
                    name="Created"
                    radius={[4, 4, 0, 0]}
                  />
                  <Bar
                    dataKey="completed"
                    fill={CHART_COLORS[1]}
                    name="Completed"
                    radius={[4, 4, 0, 0]}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </Col>
          <Col span={8}>
            {pieData.length > 0 && (
              <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={70}
                    dataKey="value"
                    nameKey="name"
                    label={({ name, value }: { name: string; value: number }) =>
                      `${name}: ${value}`
                    }
                    labelLine={false}
                  >
                    {pieData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={STATUS_PIE_COLORS[entry.name] || "#595959"}
                      />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: "#1f1f1f",
                      border: "1px solid #303030",
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </Col>
        </Row>
      </Spin>
    </Card>
  );
}
