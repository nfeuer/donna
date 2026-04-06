import { Card, Row, Col, Empty } from "antd";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  LineChart,
  Line,
} from "recharts";
import { CHART_COLORS } from "../../theme/darkTheme";
import type { ShadowComparison, ShadowStats } from "../../api/shadow";

interface Props {
  comparisons: ShadowComparison[];
  stats: ShadowStats | null;
}

export default function ShadowCharts({ comparisons, stats }: Props) {
  const scatterData = comparisons
    .filter((c) => c.primary.quality_score !== null && c.shadow.quality_score !== null)
    .map((c) => ({
      primary: c.primary.quality_score,
      shadow: c.shadow.quality_score,
      taskType: c.primary.task_type,
    }));

  const trendData = stats?.trend ?? [];

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <Card title="Quality Scatter: Primary vs Shadow" size="small">
          {scatterData.length === 0 ? (
            <Empty description="No quality scores to compare" style={{ padding: 40 }} />
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                <XAxis
                  type="number"
                  dataKey="primary"
                  name="Primary Quality"
                  domain={[0, 1]}
                  tick={{ fill: "#8c8c8c", fontSize: 11 }}
                  label={{ value: "Primary", position: "bottom", fill: "#8c8c8c", fontSize: 11 }}
                />
                <YAxis
                  type="number"
                  dataKey="shadow"
                  name="Shadow Quality"
                  domain={[0, 1]}
                  tick={{ fill: "#8c8c8c", fontSize: 11 }}
                  label={{ value: "Shadow", angle: -90, position: "insideLeft", fill: "#8c8c8c", fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{ background: "#1f1f1f", border: "1px solid #303030", fontSize: 12 }}
                  formatter={(value: number) => value.toFixed(3)}
                />
                <ReferenceLine
                  segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
                  stroke="#8c8c8c"
                  strokeDasharray="3 3"
                  label={{ value: "Equal", fill: "#8c8c8c", fontSize: 10 }}
                />
                <Scatter data={scatterData} fill={CHART_COLORS[0]} fillOpacity={0.7} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </Card>
      </Col>
      <Col xs={24} lg={12}>
        <Card title="Shadow Quality Trend" size="small">
          {trendData.length === 0 ? (
            <Empty description="No trend data available yet" style={{ padding: 40 }} />
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={trendData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#8c8c8c", fontSize: 11 }}
                />
                <YAxis
                  domain={[0, 1]}
                  tick={{ fill: "#8c8c8c", fontSize: 11 }}
                  label={{ value: "Avg Quality", angle: -90, position: "insideLeft", fill: "#8c8c8c", fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{ background: "#1f1f1f", border: "1px solid #303030", fontSize: 12 }}
                  formatter={(value: number) => value.toFixed(4)}
                />
                <Line
                  type="monotone"
                  dataKey="avg_quality"
                  stroke={CHART_COLORS[1]}
                  strokeWidth={2}
                  dot={{ r: 3, fill: CHART_COLORS[1] }}
                  name="Avg Quality"
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </Col>
    </Row>
  );
}
