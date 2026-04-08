import { Card, Statistic, Row, Col, Table, Tag, Skeleton } from "antd";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Bar,
  ComposedChart,
} from "recharts";
import type { ParseAccuracyData } from "../../api/dashboard";
import {
  STATUS_COLORS,
  CHART_TOOLTIP_STYLE,
  CHART_GRID_STROKE,
  CHART_TICK,
} from "../../theme/darkTheme";

interface Props {
  data: ParseAccuracyData | null;
  loading: boolean;
}

export default function ParseAccuracyCard({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <Card title="Parse Accuracy" size="small" styles={{ body: { padding: "12px 16px" } }}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </Card>
    );
  }

  const accuracyColor = (pct: number) =>
    pct >= 90
      ? STATUS_COLORS.SUCCESS
      : pct >= 80
        ? STATUS_COLORS.WARNING
        : STATUS_COLORS.ERROR;

  const s = data?.summary;

  return (
    <Card
      title="Parse Accuracy"
      size="small"
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Statistic
            title="Accuracy"
            value={s?.accuracy_pct ?? 0}
            suffix="%"
            valueStyle={{
              color: s ? accuracyColor(s.accuracy_pct) : undefined,
              fontSize: 22,
            }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Parses"
            value={s?.total_parses ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Corrections"
            value={s?.total_corrections ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Most Corrected"
            value={s?.most_corrected_field ?? "—"}
            valueStyle={{ fontSize: 14 }}
          />
        </Col>
      </Row>

      {data?.time_series && data.time_series.length > 0 && (
        <div role="img" aria-label={`Parse accuracy trend over ${data.days} days`}>
          <ResponsiveContainer width="100%" height={180}>
            <ComposedChart data={data.time_series}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_STROKE} />
              <XAxis
                dataKey="date"
                tick={CHART_TICK}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis
                yAxisId="pct"
                domain={[0, 100]}
                tick={CHART_TICK}
                tickFormatter={(v: number) => `${v}%`}
              />
              <YAxis
                yAxisId="count"
                orientation="right"
                tick={CHART_TICK}
              />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Area
                yAxisId="pct"
                type="monotone"
                dataKey="accuracy"
                stroke="#52c41a"
                fill="#52c41a"
                fillOpacity={0.15}
                name="Accuracy %"
              />
              <Bar
                yAxisId="count"
                dataKey="corrections"
                fill="#ff4d4f"
                opacity={0.6}
                name="Corrections"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {data?.field_breakdown && data.field_breakdown.length > 0 && (
        <Table
          size="small"
          dataSource={data.field_breakdown.slice(0, 5)}
          rowKey="field"
          pagination={false}
          style={{ marginTop: 12 }}
          columns={[
            {
              title: "Field",
              dataIndex: "field",
              render: (v: string) => <Tag>{v}</Tag>,
            },
            { title: "Corrections", dataIndex: "count", width: 100 },
            {
              title: "% of Total",
              dataIndex: "count",
              width: 100,
              render: (v: number) =>
                s
                  ? `${((v / s.total_corrections) * 100).toFixed(1)}%`
                  : "—",
            },
          ]}
        />
      )}
    </Card>
  );
}
