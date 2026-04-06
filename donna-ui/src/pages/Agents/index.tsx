import { useState, useEffect, useCallback } from "react";
import { Row, Col, Typography, Button, Space } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import RefreshButton from "../../components/RefreshButton";
import AgentCard from "./AgentCard";
import AgentDetailView from "./AgentDetail";
import { fetchAgents, type AgentSummary } from "../../api/agents";

const { Title } = Typography;

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAgents();
      setAgents(data);
    } catch {
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  if (selected) {
    return (
      <div>
        <Space style={{ marginBottom: 16 }}>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => setSelected(null)}
          >
            All Agents
          </Button>
          <Title level={4} style={{ margin: 0, textTransform: "capitalize" }}>
            {selected} Agent
          </Title>
        </Space>
        <AgentDetailView agentName={selected} />
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>Agents</Title>
        <RefreshButton onRefresh={doFetch} />
      </div>
      <Row gutter={[16, 16]}>
        {(loading ? Array(6).fill(null) : agents).map((agent, i) => (
          <Col xs={24} sm={12} lg={8} key={agent?.name ?? i}>
            {agent ? (
              <AgentCard
                agent={agent}
                selected={selected === agent.name}
                onClick={() => setSelected(agent.name)}
              />
            ) : (
              <div style={{ height: 160, background: "#1f1f1f", borderRadius: 6 }} />
            )}
          </Col>
        ))}
      </Row>
    </div>
  );
}
