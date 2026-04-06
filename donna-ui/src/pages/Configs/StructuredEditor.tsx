import AgentsForm from "./forms/AgentsForm";
import ModelsForm from "./forms/ModelsForm";
import TaskTypesForm from "./forms/TaskTypesForm";
import StatesForm from "./forms/StatesForm";
import RawYamlEditor from "./RawYamlEditor";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  filename: string;
  data: Record<string, any>;
  rawYaml: string;
  onDataChange: (data: Record<string, any>) => void;
  onRawChange: (yaml: string) => void;
}

const STRUCTURED_FILES: Record<
  string,
  React.ComponentType<{ data: any; onChange: (d: any) => void }>
> = {
  "agents.yaml": AgentsForm,
  "donna_models.yaml": ModelsForm,
  "task_types.yaml": TaskTypesForm,
  "task_states.yaml": StatesForm,
};

export default function StructuredEditor({
  filename,
  data,
  rawYaml,
  onDataChange,
  onRawChange,
}: Props) {
  const FormComponent = STRUCTURED_FILES[filename];

  if (FormComponent) {
    return <FormComponent data={data} onChange={onDataChange} />;
  }

  // Fallback to raw YAML editor for files without structured forms
  return <RawYamlEditor value={rawYaml} onChange={onRawChange} />;
}
