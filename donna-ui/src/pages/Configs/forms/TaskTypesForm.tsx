import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Select, SelectItem } from "../../../primitives/Select";
import { Pill } from "../../../primitives/Pill";
import { taskTypesSchema, type TaskTypesConfig } from "../schemas";
import styles from "./Forms.module.css";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const MODEL_OPTIONS = ["parser", "reasoner", "fallback", "local_parser"];
const NONE = "__none__";

export default function TaskTypesForm({ data, onChange }: Props) {
  const form = useForm<TaskTypesConfig>({
    values: data as TaskTypesConfig,
    resolver: zodResolver(taskTypesSchema) as any,
    mode: "onChange",
  });

  useEffect(() => {
    const sub = form.watch((values) => onChange(values as Record<string, any>));
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const taskTypes = form.watch("task_types") ?? {};

  return (
    <form onSubmit={(e) => e.preventDefault()} className={styles.stackTight}>
      {Object.entries(taskTypes).map(([name, cfg]) => (
        <Card key={name}>
          <details open>
            <summary className={styles.detailsSummary}>
              <strong>{name}</strong>{" "}
              <span className="sr-only">model:</span>
              <Pill variant="accent">{cfg?.model ?? "—"}</Pill>
            </summary>

            <div className={styles.detailsBody}>
              <FormField label="Description">
                {(fieldProps) => (
                  <Input
                    {...fieldProps}
                    {...form.register(`task_types.${name}.description` as const)}
                  />
                )}
              </FormField>

              <FormField label="Model">
                {() => (
                  <Select
                    value={form.watch(`task_types.${name}.model`) ?? ""}
                    onValueChange={(v) =>
                      form.setValue(`task_types.${name}.model`, v, { shouldDirty: true })
                    }
                  >
                    {MODEL_OPTIONS.map((m) => (
                      <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                  </Select>
                )}
              </FormField>

              <FormField label="Shadow model">
                {() => (
                  <Select
                    value={form.watch(`task_types.${name}.shadow`) ?? NONE}
                    onValueChange={(v) =>
                      form.setValue(
                        `task_types.${name}.shadow`,
                        v === NONE ? undefined : v,
                        { shouldDirty: true },
                      )
                    }
                  >
                    <SelectItem value={NONE}>(none)</SelectItem>
                    {MODEL_OPTIONS.map((m) => (
                      <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                  </Select>
                )}
              </FormField>

              <FormField label="Prompt template">
                {(fieldProps) => (
                  <Input
                    {...fieldProps}
                    {...form.register(`task_types.${name}.prompt_template` as const)}
                  />
                )}
              </FormField>

              <FormField label="Output schema">
                {(fieldProps) => (
                  <Input
                    {...fieldProps}
                    {...form.register(`task_types.${name}.output_schema` as const)}
                  />
                )}
              </FormField>

              <FormField label="Tools (comma-separated)">
                {(fieldProps) => (
                  <Input
                    {...fieldProps}
                    value={(form.watch(`task_types.${name}.tools`) ?? []).join(", ")}
                    onChange={(e) =>
                      form.setValue(
                        `task_types.${name}.tools`,
                        e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                        { shouldDirty: true },
                      )
                    }
                  />
                )}
              </FormField>
            </div>
          </details>
        </Card>
      ))}
    </form>
  );
}
