import { useState, useEffect, useCallback } from "react";
import { Button, Space, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";

const { Text } = Typography;

interface RefreshButtonProps {
  onRefresh: () => Promise<void>;
  autoRefreshMs?: number;
}

export default function RefreshButton({
  onRefresh,
  autoRefreshMs,
}: RefreshButtonProps) {
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [ago, setAgo] = useState("");

  const doRefresh = useCallback(async () => {
    setLoading(true);
    try {
      await onRefresh();
      setLastUpdated(new Date());
    } finally {
      setLoading(false);
    }
  }, [onRefresh]);

  // Auto-refresh interval
  useEffect(() => {
    if (!autoRefreshMs) return;
    const id = setInterval(doRefresh, autoRefreshMs);
    return () => clearInterval(id);
  }, [autoRefreshMs, doRefresh]);

  // Update "ago" text every 5s
  useEffect(() => {
    const tick = () => {
      if (!lastUpdated) return;
      const secs = Math.floor((Date.now() - lastUpdated.getTime()) / 1000);
      if (secs < 5) setAgo("just now");
      else if (secs < 60) setAgo(`${secs}s ago`);
      else setAgo(`${Math.floor(secs / 60)}m ago`);
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, [lastUpdated]);

  return (
    <Space>
      {lastUpdated && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {ago}
        </Text>
      )}
      <Button
        icon={<ReloadOutlined spin={loading} />}
        size="small"
        onClick={doRefresh}
        loading={loading}
      >
        Refresh
      </Button>
    </Space>
  );
}
