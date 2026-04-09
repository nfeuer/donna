import Editor from "@monaco-editor/react";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function RawYamlEditor({ value, onChange }: Props) {
  return (
    <Editor
      height="min(calc(100vh - 280px), 640px)"
      language="yaml"
      theme={DONNA_MONACO_THEME}
      beforeMount={setupDonnaMonacoTheme}
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
