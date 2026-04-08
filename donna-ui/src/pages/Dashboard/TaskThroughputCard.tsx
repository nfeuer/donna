import { Card, Statistic, Row, Col, Skeleton } from "antd";
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
  Legend,
} from "recharts";
import type { TaskThroughputData } from "../../api/dashboard";
import {
  CHART_COLORS,
  STATUS_COLORS,
  TASK_STATUS_COLORS,
  CHART_TOOLTIP_STYLE,
  CHART_GRID_STROKE,
  CHART_TICK,
} from "../../theme/darkTheme";

interface Props {
  data: TaskThroughputData | null;
  loading: boolean;
}

export default function TaskThroughputCard({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <Card title="Task Throughput" size="small" styles={{ body: { padding: "12px 16px" } }}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </Card>
    );
  }

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
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="Created"
            value={s?.total_created ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="Completed"
            value={s?.total_completed ?? 0}
            valueStyle={{ fontSize: 22, color: STATUS_COLORS.SUCCESS }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="Completion"
            value={s?.completion_rate ?? 0}
            suffix="%"
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="Avg Hours"
            value={s?.avg_completion_hours ?? "—"}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
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
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="Reschedules"
            value={s?.avg_reschedules ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
      </Row>

      <Row gutter={16}>
        <Col xs={24} md={16}>
          {data?.time_series && data.time_series.length > 0 && (
            <div role="img" aria-label={`Task creation and completion trend over ${data.days} days`}>
              <ResponsiveContainer width="100%" height={180}>
                <ComposedChart data={data.time_series}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_STROKE} />
                  <XAxis
                    dataKey="date"
                    tick={CHART_TICK}
                    tickFormatter={(v: string) => v.slice(5)}
                  />
                  <YAxis tick={CHART_TICK} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
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
            </div>
          )}
        </Col>
        <Col xs={24} md={8}>
          {pieData.length > 0 && (
            <div role="img" aria-label="Task status distribution">
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
                  >
                    {pieData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={TASK_STATUS_COLORS[entry.name] || "#595959"}
                      />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={(value: string) => (
                      <span style={{ color: CHART_TICK.fill, fontSize: 11 }}>{value}</span>
                    )}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </Col>
      </Row>
    </Card>
  );
}
