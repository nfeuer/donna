import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Pill } from "../../../primitives/Pill";
import { DataTable } from "../../../primitives/DataTable";
import { stateCssVar, statePillVariant } from "../../../theme/stateColors";
import { statesSchema, type StatesConfig } from "../schemas";
import styles from "./Forms.module.css";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const STATE_POSITIONS: Record<string, { x: number; y: number }> = {
  backlog: { x: 80, y: 140 },
  scheduled: { x: 250, y: 60 },
  in_progress: { x: 420, y: 140 },
  blocked: { x: 420, y: 260 },
  waiting_input: { x: 250, y: 260 },
  done: { x: 590, y: 60 },
  cancelled: { x: 590, y: 260 },
};

interface Transition {
  from: string;
  to: string;
  trigger: string;
  side_effects?: string[];
}

function StateMachineDiagram({ transitions }: { transitions: Transition[] }) {
  const getTransitionPath = (from: string, to: string): string => {
    const start = STATE_POSITIONS[from];
    const end = STATE_POSITIONS[to];
    if (!start || !end) return "";
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const nodeR = 30;
    const sx = start.x + (dx / dist) * nodeR;
    const sy = start.y + (dy / dist) * nodeR;
    const ex = end.x - (dx / dist) * (nodeR + 6);
    const ey = end.y - (dy / dist) * (nodeR + 6);
    const midX = (sx + ex) / 2;
    const midY = (sy + ey) / 2;
    const perpX = -(ey - sy) * 0.15;
    const perpY = (ex - sx) * 0.15;
    return `M ${sx} ${sy} Q ${midX + perpX} ${midY + perpY} ${ex} ${ey}`;
  };

  const regularTransitions = transitions.filter((t) => t.from !== "*");

  return (
    <svg
      width="100%"
      viewBox="0 0 700 320"
      role="img"
      aria-label="State machine transition diagram"
      style={{ display: "block", maxWidth: 700, margin: "0 auto" }}
    >
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="var(--color-text-muted)" />
        </marker>
      </defs>

      {regularTransitions.map((t, i) => {
        const path = getTransitionPath(t.from, t.to);
        if (!path) return null;
        return (
          <path
            key={i}
            d={path}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={1.5}
            markerEnd="url(#arrowhead)"
          />
        );
      })}

      {Object.entries(STATE_POSITIONS).map(([state, pos]) => (
        <g key={state}>
          <circle cx={pos.x} cy={pos.y} r={30} fill={stateCssVar(state)} opacity={0.85} />
          <text
            x={pos.x}
            y={pos.y + 1}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="var(--color-inset)"
            fontSize={10}
            fontWeight={600}
          >
            {state.replace("_", " ")}
          </text>
        </g>
      ))}

      <text x={10} y={310} fill="var(--color-text-muted)" fontSize={10}>
        * = any state can transition to cancelled
      </text>
    </svg>
  );
}

export default function StatesForm({ data }: Props) {
  const {
    formState: { errors },
  } = useForm<StatesConfig>({
    values: data as StatesConfig,
    resolver: zodResolver(statesSchema),
    mode: "onChange",
  });

  const states: string[] = (data.states as string[]) ?? [];
  const transitions: Transition[] = (data.transitions as Transition[]) ?? [];

  const topError = errors.root?.message ?? Object.values(errors)[0]?.message;

  return (
    <div className={styles.stack}>
      {topError && (
        <div role="alert" style={{ color: "var(--color-error)", fontSize: "var(--text-body)" }}>
          Schema error: {String(topError)}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>State machine</CardTitle>
        </CardHeader>
        <StateMachineDiagram transitions={transitions} />
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>States</CardTitle>
        </CardHeader>
        <div className={styles.stateList}>
          {states.map((s) => (
            <Pill key={s} variant={statePillVariant(s)}>{s}</Pill>
          ))}
        </div>
        <div className={styles.stateListMeta}>
          Initial state: <strong>{data.initial_state ?? "backlog"}</strong>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transitions</CardTitle>
        </CardHeader>
        <DataTable<Transition>
          data={transitions}
          getRowId={(row) => `${row.from}__${row.to}__${row.trigger}`}
          columns={[
            {
              header: "From",
              accessorKey: "from",
              cell: ({ getValue }) => {
                const v = getValue() as string;
                return v === "*"
                  ? <Pill variant="muted">*</Pill>
                  : <Pill variant={statePillVariant(v)}>{v}</Pill>;
              },
            },
            {
              header: "To",
              accessorKey: "to",
              cell: ({ getValue }) => {
                const v = getValue() as string;
                return <Pill variant={statePillVariant(v)}>{v}</Pill>;
              },
            },
            {
              header: "Trigger",
              accessorKey: "trigger",
              cell: ({ getValue }) => (
                <code style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {String(getValue() ?? "")}
                </code>
              ),
            },
            {
              header: "Side effects",
              accessorKey: "side_effects",
              cell: ({ getValue }) => {
                const effects = (getValue() as string[] | undefined) ?? [];
                if (effects.length === 0) return "—";
                return (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {effects.map((e, i) => <Pill key={i} variant="muted">{e}</Pill>)}
                  </div>
                );
              },
            },
          ]}
        />
      </Card>
    </div>
  );
}
