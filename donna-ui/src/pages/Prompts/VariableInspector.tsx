import { Card, CardHeader, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";

interface Props {
  content: string;
  schemaPath: string | null;
}

export default function VariableInspector({ content, schemaPath }: Props) {
  const matches = content.match(/\{\{\s*(\w+)\s*\}\}/g) ?? [];
  const variables = [...new Set(matches.map((m) => m.replace(/[{}\s]/g, "")))];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Template variables</CardTitle>
        {schemaPath && <Pill variant="accent">{schemaPath}</Pill>}
      </CardHeader>
      {variables.length === 0 ? (
        <div style={{ color: "var(--color-text-muted)", fontSize: "var(--text-body)" }}>
          No template variables found.
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {variables.map((v) => (
            <Pill key={v} variant="muted">{`{{ ${v} }}`}</Pill>
          ))}
        </div>
      )}
    </Card>
  );
}
