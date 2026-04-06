import { Card, Table, Tag, Space } from "antd";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

// Fixed positions for the 7 states in a flow layout
const STATE_POSITIONS: Record<string, { x: number; y: number }> = {
  backlog: { x: 80, y: 140 },
  scheduled: { x: 250, y: 60 },
  in_progress: { x: 420, y: 140 },
  blocked: { x: 420, y: 260 },
  waiting_input: { x: 250, y: 260 },
  done: { x: 590, y: 60 },
  cancelled: { x: 590, y: 260 },
};

const STATE_COLORS: Record<string, string> = {
  backlog: "#8c8c8c",
  scheduled: "#1890ff",
  in_progress: "#faad14",
  blocked: "#ff4d4f",
  waiting_input: "#fa8c16",
  done: "#52c41a",
  cancelled: "#595959",
};

function StateMachineDiagram({ transitions }: { transitions: any[] }) {
  const arrowId = "arrowhead";

  const getTransitionPath = (from: string, to: string): string => {
    const start = STATE_POSITIONS[from];
    const end = STATE_POSITIONS[to];
    if (!start || !end) return "";

    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const nodeR = 30;

    // Start and end offset by node radius
    const sx = start.x + (dx / dist) * nodeR;
    const sy = start.y + (dy / dist) * nodeR;
    const ex = end.x - (dx / dist) * (nodeR + 6);
    const ey = end.y - (dy / dist) * (nodeR + 6);

    // Curve offset for bidirectional arrows
    const midX = (sx + ex) / 2;
    const midY = (sy + ey) / 2;
    const perpX = -(ey - sy) * 0.15;
    const perpY = (ex - sx) * 0.15;

    return `M ${sx} ${sy} Q ${midX + perpX} ${midY + perpY} ${ex} ${ey}`;
  };

  // Wildcard transitions
  const regularTransitions = transitions.filter((t) => t.from !== "*");

  return (
    <svg width="700" height="320" style={{ display: "block", margin: "0 auto" }}>
      <defs>
        <marker id={arrowId} markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="#666" />
        </marker>
      </defs>

      {/* Transition arrows */}
      {regularTransitions.map((t: any, i: number) => {
        const path = getTransitionPath(t.from, t.to);
        if (!path) return null;
        return (
          <path
            key={i}
            d={path}
            fill="none"
            stroke="#555"
            strokeWidth={1.5}
            markerEnd={`url(#${arrowId})`}
          />
        );
      })}

      {/* State nodes */}
      {Object.entries(STATE_POSITIONS).map(([state, pos]) => (
        <g key={state}>
          <circle
            cx={pos.x}
            cy={pos.y}
            r={30}
            fill={STATE_COLORS[state]}
            opacity={0.85}
          />
          <text
            x={pos.x}
            y={pos.y + 1}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="white"
            fontSize={10}
            fontWeight={600}
          >
            {state.replace("_", " ")}
          </text>
        </g>
      ))}

      {/* Legend: wildcard indicator */}
      <text x={10} y={310} fill="#666" fontSize={10}>
        * = any state can transition to cancelled
      </text>
    </svg>
  );
}

export default function StatesForm({ data }: Props) {
  const states: string[] = data.states ?? [];
  const transitions: any[] = data.transitions ?? [];

  return (
    <div style={{ maxHeight: "calc(100vh - 290px)", overflow: "auto", paddingRight: 8 }}>
      {/* State diagram */}
      <Card size="small" title="State Machine Diagram" style={{ marginBottom: 16 }}>
        <StateMachineDiagram transitions={transitions} />
      </Card>

      {/* States list */}
      <Card size="small" title="States" style={{ marginBottom: 16 }}>
        <Space wrap>
          {states.map((s) => (
            <Tag key={s} color={STATE_COLORS[s]} style={{ fontSize: 13, padding: "2px 10px" }}>
              {s}
            </Tag>
          ))}
        </Space>
        <div style={{ marginTop: 8, fontSize: 12, color: "#666" }}>
          Initial state: <strong>{data.initial_state ?? "backlog"}</strong>
        </div>
      </Card>

      {/* Transitions table */}
      <Card size="small" title="Transitions">
        <Table
          dataSource={transitions.map((t: any, i: number) => ({ ...t, key: i }))}
          size="small"
          pagination={false}
          columns={[
            {
              title: "From",
              dataIndex: "from",
              width: 120,
              render: (v: string) => (
                <Tag color={v === "*" ? "default" : STATE_COLORS[v]}>{v}</Tag>
              ),
            },
            {
              title: "To",
              dataIndex: "to",
              width: 120,
              render: (v: string) => <Tag color={STATE_COLORS[v]}>{v}</Tag>,
            },
            {
              title: "Trigger",
              dataIndex: "trigger",
              width: 220,
              render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code>,
            },
            {
              title: "Side Effects",
              dataIndex: "side_effects",
              render: (effects: string[]) =>
                effects?.map((e, i) => (
                  <Tag key={i} style={{ fontSize: 11, marginBottom: 2 }}>
                    {e}
                  </Tag>
                )) ?? "—",
            },
          ]}
        />
      </Card>
    </div>
  );
}
