import { Menu, Spin } from "antd";
import { FileMarkdownOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { PromptFile } from "../../api/configs";

interface Props {
  files: PromptFile[];
  loading: boolean;
  selected: string | null;
  onSelect: (name: string) => void;
}

export default function PromptFileList({ files, loading, selected, onSelect }: Props) {
  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 20 }}>
        <Spin size="small" />
      </div>
    );
  }

  return (
    <Menu
      mode="inline"
      selectedKeys={selected ? [selected] : []}
      onClick={({ key }) => onSelect(key)}
      style={{ background: "transparent", border: "none" }}
      items={files.map((f) => ({
        key: f.name,
        icon: <FileMarkdownOutlined />,
        label: (
          <div>
            <div style={{ fontSize: 13 }}>{f.name.replace(".md", "")}</div>
            <div style={{ fontSize: 10, color: "#666" }}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </div>
          </div>
        ),
      }))}
    />
  );
}
