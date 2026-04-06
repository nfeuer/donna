import { Card, Tag, Typography, List } from "antd";

const { Title, Paragraph } = Typography;

interface PageShellProps {
  title: string;
  description: string;
  session: number;
  features: string[];
}

export default function PageShell({
  title,
  description,
  session,
  features,
}: PageShellProps) {
  return (
    <Card
      style={{ maxWidth: 700, margin: "60px auto" }}
      styles={{ body: { textAlign: "center" } }}
    >
      <Title level={3}>{title}</Title>
      <Tag color={session === 2 ? "blue" : "purple"}>Session {session}</Tag>
      <Paragraph style={{ marginTop: 16, color: "#8c8c8c" }}>
        {description}
      </Paragraph>
      <List
        size="small"
        dataSource={features}
        style={{ textAlign: "left", marginTop: 24 }}
        renderItem={(item) => (
          <List.Item style={{ color: "#8c8c8c", borderColor: "#303030" }}>
            {item}
          </List.Item>
        )}
      />
    </Card>
  );
}
