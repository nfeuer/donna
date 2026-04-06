import { Modal } from "antd";
import { DiffEditor } from "@monaco-editor/react";

interface Props {
  open: boolean;
  original: string;
  modified: string;
  filename: string;
  saving: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function SaveDiffModal({
  open,
  original,
  modified,
  filename,
  saving,
  onConfirm,
  onCancel,
}: Props) {
  return (
    <Modal
      title={`Save changes to ${filename}?`}
      open={open}
      onOk={onConfirm}
      onCancel={onCancel}
      okText="Save"
      confirmLoading={saving}
      width={900}
      styles={{ body: { padding: 0 } }}
    >
      <DiffEditor
        height="400px"
        language="yaml"
        theme="vs-dark"
        original={original}
        modified={modified}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          scrollBeyondLastLine: false,
          renderSideBySide: true,
        }}
      />
    </Modal>
  );
}
