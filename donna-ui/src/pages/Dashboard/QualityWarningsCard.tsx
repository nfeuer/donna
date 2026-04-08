import { Card, Statistic, Row, Col, Table, Tag, Skeleton, Typography } from "antd";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
} from "recharts";
import type { QualityWarningsData } from "../../api/dashboard";
import {
  STATUS_COLORS,
  CHART_TOOLTIP_STYLE,
  CHART_GRID_STROKE,
  CHART_TICK,
} from "../../theme/darkTheme";

const { Text } = Typography;

interface Props {
  data: QualityWarningsData | null;
  loading: boolean;
}

export default function QualityWarningsCard({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <Card title="Quality Warnings" size="small" styles={{ body: { padding: "12px 16px" } }}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </Card>
    );
  }

  const s = data?.summary;

  return (
    <Card
      title="Quality Warnings"
      size="small"
      extra={
        data?.thresholds && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            warn &lt; {data.thresholds.warning_threshold} | crit &lt;{" "}
            {data.thresholds.critical_threshold}
          </Text>
        )
      }
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Statistic
            title="Warning Rate"
            value={s?.warning_rate_pct ?? 0}
            suffix="%"
            valueStyle={{
              color:
                s && s.warning_rate_pct > 10
                  ? STATUS_COLORS.ERROR
                  : s && s.warning_rate_pct > 5
                    ? STATUS_COLORS.WARNING
                    : STATUS_COLORS.SUCCESS,
              fontSize: 22,
            }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Warnings"
            value={s?.total_warnings ?? 0}
            valueStyle={{ color: STATUS_COLORS.WARNING, fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Criticals"
            value={s?.total_criticals ?? 0}
            valueStyle={{ color: STATUS_COLORS.ERROR, fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="Total Scored"
            value={s?.total_scored ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
      </Row>

      {data?.time_series && data.time_series.length > 0 && (
        <div role="img" aria-label={`Quality warnings trend over ${data.days} days`}>
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
              <Area
                type="monotone"
                dataKey="warnings"
                stroke={STATUS_COLORS.WARNING}
                fill={STATUS_COLORS.WARNING}
                fillOpacity={0.15}
                stackId="1"
                name="Warnings"
              />
              <Area
                type="monotone"
                dataKey="criticals"
                stroke={STATUS_COLORS.ERROR}
                fill={STATUS_COLORS.ERROR}
                fillOpacity={0.25}
                stackId="1"
                name="Criticals"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {data?.by_task_type && data.by_task_type.length > 0 && (
        <Table
          size="small"
          dataSource={data.by_task_type.slice(0, 5)}
          rowKey="task_type"
          pagination={false}
          style={{ marginTop: 12 }}
          scroll={{ x: 360 }}
          columns={[
            {
              title: "Task Type",
              dataIndex: "task_type",
              render: (v: string) => <Tag>{v}</Tag>,
            },
            {
              title: "Warnings",
              dataIndex: "warnings",
              width: 90,
              render: (v: number) => (
                <span style={{ color: STATUS_COLORS.WARNING }}>{v}</span>
              ),
            },
            {
              title: "Criticals",
              dataIndex: "criticals",
              width: 90,
              render: (v: number) => (
                <span style={{ color: STATUS_COLORS.ERROR }}>{v}</span>
              ),
            },
            {
              title: "Scored",
              dataIndex: "total_scored",
              width: 80,
            },
          ]}
        />
      )}
    </Card>
  );
}
