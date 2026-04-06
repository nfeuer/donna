import Editor from "@monaco-editor/react";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function RawYamlEditor({ value, onChange }: Props) {
  return (
    <Editor
      height="calc(100vh - 260px)"
      language="yaml"
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
      }}
    />
  );
}
