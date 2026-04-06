import { useState, useCallback, useEffect } from "react";
import {
  Layout,
  Input,
  Select,
  DatePicker,
  Space,
  Card,
  Tag,
} from "antd";
import dayjs from "dayjs";
import RefreshButton from "../../components/RefreshButton";
import EventTypeTree from "./EventTypeTree";
import LogTable from "./LogTable";
import TraceView from "./TraceView";
import { fetchLogs, type LogEntry, type LogFilters } from "../../api/logs";

const { Sider, Content } = Layout;
const { RangePicker } = DatePicker;
const { Search } = Input;

const LEVEL_OPTIONS = [
  { label: "All Levels", value: "" },
  { label: "DEBUG", value: "DEBUG" },
  { label: "INFO", value: "INFO" },
  { label: "WARNING", value: "WARNING" },
  { label: "ERROR", value: "ERROR" },
  { label: "CRITICAL", value: "CRITICAL" },
];

export default function Logs() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  // Filters
  const [selectedEventTypes, setSelectedEventTypes] = useState<string[]>([]);
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [dateRange, setDateRange] = useState<
    [dayjs.Dayjs | null, dayjs.Dayjs | null] | null
  >(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Trace drawer
  const [traceId, setTraceId] = useState<string | null>(null);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const filters: LogFilters = {
        limit: pageSize,
        offset: (page - 1) * pageSize,
      };
      if (selectedEventTypes.length > 0) {
        filters.event_type = selectedEventTypes.join(",");
      }
      if (level) filters.level = level;
      if (search) filters.search = search;
      if (dateRange?.[0]) filters.start = dateRange[0].toISOString();
      if (dateRange?.[1]) filters.end = dateRange[1].toISOString();

      const resp = await fetchLogs(filters);
      setEntries(resp.entries);
      setTotal(resp.total);
      setSource(resp.source);
    } catch {
      setEntries([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [selectedEventTypes, level, search, dateRange, page, pageSize]);

  // Fetch on filter change
  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handlePageChange = (newPage: number, newSize: number) => {
    setPage(newPage);
    setPageSize(newSize);
  };

  return (
    <Layout style={{ background: "transparent", minHeight: "calc(100vh - 130px)" }}>
      <Sider
        width={240}
        style={{
          background: "#1f1f1f",
          borderRadius: 6,
          padding: 12,
          marginRight: 16,
          overflow: "auto",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>
          Event Types
        </div>
        <EventTypeTree
          selected={selectedEventTypes}
          onChange={setSelectedEventTypes}
        />
      </Sider>

      <Content>
        <Card size="small" style={{ marginBottom: 12 }}>
          <Space wrap>
            <RangePicker
              size="small"
              showTime
              onChange={(dates) =>
                setDateRange(dates as [dayjs.Dayjs, dayjs.Dayjs] | null)
              }
            />
            <Select
              size="small"
              value={level}
              onChange={setLevel}
              options={LEVEL_OPTIONS}
              style={{ width: 130 }}
            />
            <Search
              size="small"
              placeholder="Search logs..."
              allowClear
              onSearch={setSearch}
              style={{ width: 200 }}
            />
            <Tag>{source || "—"}</Tag>
            <RefreshButton onRefresh={doFetch} />
          </Space>
        </Card>

        <LogTable
          entries={entries}
          total={total}
          loading={loading}
          page={page}
          pageSize={pageSize}
          onPageChange={handlePageChange}
          onCorrelationClick={setTraceId}
          onTaskClick={(id) => window.open(`/tasks/${id}`, "_blank")}
        />

        <TraceView
          correlationId={traceId}
          onClose={() => setTraceId(null)}
        />
      </Content>
    </Layout>
  );
}
