import { Menu, Spin } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { ConfigFile } from "../../api/configs";

interface Props {
  files: ConfigFile[];
  loading: boolean;
  selected: string | null;
  onSelect: (name: string) => void;
}

export default function ConfigFileList({ files, loading, selected, onSelect }: Props) {
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
        icon: <FileTextOutlined />,
        label: (
          <div>
            <div style={{ fontSize: 13 }}>{f.name}</div>
            <div style={{ fontSize: 10, color: "#666" }}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </div>
          </div>
        ),
      }))}
    />
  );
}
