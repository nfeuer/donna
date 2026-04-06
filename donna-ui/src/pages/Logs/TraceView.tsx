import { useEffect, useState } from "react";
import { Drawer, Timeline, Tag, Spin, Typography, Descriptions } from "antd";
import { fetchTrace, type LogEntry } from "../../api/logs";
import { LEVEL_COLORS } from "../../theme/darkTheme";

const { Text } = Typography;

interface Props {
  correlationId: string | null;
  onClose: () => void;
}

export default function TraceView({ correlationId, onClose }: Props) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  useEffect(() => {
    if (!correlationId) return;
    setLoading(true);
    fetchTrace(correlationId)
      .then((resp) => {
        setEntries(resp.entries);
        setSource(resp.source);
      })
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [correlationId]);

  const totalDurationMs =
    entries.length >= 2
      ? new Date(entries[entries.length - 1].timestamp).getTime() -
        new Date(entries[0].timestamp).getTime()
      : 0;

  return (
    <Drawer
      title={`Trace: ${correlationId?.slice(0, 12) ?? ""}...`}
      open={!!correlationId}
      onClose={onClose}
      width={560}
      styles={{ body: { padding: "16px 24px" } }}
    >
      <Spin spinning={loading}>
        <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
          <Descriptions.Item label="Correlation ID">
            <Text copyable style={{ fontSize: 11, fontFamily: "monospace" }}>
              {correlationId}
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="Events">{entries.length}</Descriptions.Item>
          <Descriptions.Item label="Duration">
            {totalDurationMs > 0 ? `${totalDurationMs}ms` : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Source">
            <Tag>{source}</Tag>
          </Descriptions.Item>
        </Descriptions>

        <Timeline
          items={entries.map((entry) => ({
            color: LEVEL_COLORS[entry.level?.toUpperCase()] || "#8c8c8c",
            children: (
              <div>
                <div style={{ marginBottom: 4 }}>
                  <Tag
                    color={
                      LEVEL_COLORS[entry.level?.toUpperCase()] || "#8c8c8c"
                    }
                    style={{ fontSize: 10 }}
                  >
                    {entry.level?.toUpperCase()}
                  </Tag>
                  <Tag style={{ fontSize: 10 }}>{entry.event_type}</Tag>
                  {entry.service && (
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      {entry.service}
                    </Text>
                  )}
                </div>
                <Text style={{ fontSize: 12 }}>{entry.message}</Text>
                <div style={{ marginTop: 2 }}>
                  <Text
                    type="secondary"
                    style={{ fontSize: 10, fontFamily: "monospace" }}
                  >
                    {entry.timestamp
                      ? entry.timestamp.replace("T", " ").slice(0, 23)
                      : ""}
                    {entry.duration_ms != null && ` (${entry.duration_ms}ms)`}
                    {entry.cost_usd != null &&
                      ` $${entry.cost_usd.toFixed(4)}`}
                  </Text>
                </div>
              </div>
            ),
          }))}
        />
      </Spin>
    </Drawer>
  );
}
