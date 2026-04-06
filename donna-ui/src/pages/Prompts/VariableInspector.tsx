import { Card, Tag, Empty } from "antd";

interface Props {
  content: string;
  schemaPath: string | null;
}

export default function VariableInspector({ content, schemaPath }: Props) {
  // Extract {{ variable }} patterns
  const matches = content.match(/\{\{\s*(\w+)\s*\}\}/g) ?? [];
  const variables = [...new Set(matches.map((m) => m.replace(/[{}\s]/g, "")))];

  return (
    <Card
      size="small"
      title="Template Variables"
      style={{ marginTop: 8 }}
      extra={
        schemaPath ? (
          <Tag color="blue">{schemaPath}</Tag>
        ) : null
      }
    >
      {variables.length === 0 ? (
        <Empty
          description="No template variables found"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <div>
          {variables.map((v) => (
            <Tag key={v} color="geekblue" style={{ marginBottom: 4 }}>
              {"{{ "}
              {v}
              {" }}"}
            </Tag>
          ))}
        </div>
      )}
    </Card>
  );
}
