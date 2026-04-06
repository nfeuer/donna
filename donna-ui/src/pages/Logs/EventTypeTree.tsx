import { useEffect, useState, useMemo } from "react";
import { Tree, Button, Space, Spin } from "antd";
import type { DataNode } from "antd/es/tree";
import { fetchEventTypes } from "../../api/logs";

interface Props {
  selected: string[];
  onChange: (selected: string[]) => void;
}

export default function EventTypeTree({ selected, onChange }: Props) {
  const [tree, setTree] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchEventTypes()
      .then(setTree)
      .catch(() => setTree({}))
      .finally(() => setLoading(false));
  }, []);

  const treeData: DataNode[] = useMemo(
    () =>
      Object.entries(tree).map(([category, events]) => ({
        title: category,
        key: category,
        children: events.map((evt) => ({
          title: evt,
          key: `${category}.${evt}`,
        })),
      })),
    [tree],
  );

  const allKeys = useMemo(
    () =>
      Object.entries(tree).flatMap(([cat, evts]) =>
        evts.map((e) => `${cat}.${e}`),
      ),
    [tree],
  );

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Button size="small" onClick={() => onChange(allKeys)}>
          All
        </Button>
        <Button size="small" onClick={() => onChange([])}>
          Clear
        </Button>
      </Space>
      <Spin spinning={loading}>
        <Tree
          checkable
          checkedKeys={selected}
          onCheck={(keys) => onChange(keys as string[])}
          treeData={treeData}
          defaultExpandAll
          style={{ background: "transparent" }}
        />
      </Spin>
    </div>
  );
}
