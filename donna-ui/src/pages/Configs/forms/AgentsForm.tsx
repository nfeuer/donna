import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Select, SelectItem } from "../../../primitives/Select";
import { Switch } from "../../../primitives/Switch";
import { Checkbox } from "../../../primitives/Checkbox";
import { agentsSchema, type AgentsConfig } from "../schemas";
import styles from "./Forms.module.css";
import { cn } from "../../../lib/cn";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const ALL_TOOLS = [
  "task_db_read", "task_db_write", "calendar_read", "calendar_write",
  "web_search", "email_read", "email_draft", "notes_read",
  "fs_read", "fs_write", "github_read", "github_write",
  "docs_write", "discord_write", "cost_summary",
];

export default function AgentsForm({ data, onChange }: Props) {
  const form = useForm<AgentsConfig>({
    values: data as AgentsConfig,
    resolver: zodResolver(agentsSchema) as any,
    mode: "onChange",
  });

  useEffect(() => {
    const sub = form.watch((values) => onChange(values as Record<string, any>));
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const agents = form.watch("agents") ?? {};

  return (
    <form onSubmit={(e) => e.preventDefault()} className={styles.autoFitGrid}>
      {Object.entries(agents).map(([name, cfg]) => {
        const selectedTools = new Set<string>(cfg?.allowed_tools ?? []);
        return (
          <Card key={name}>
            <CardHeader>
              <CardTitle style={{ textTransform: "capitalize" }}>{name}</CardTitle>
              <Switch
                checked={!!cfg?.enabled}
                onCheckedChange={(v) =>
                  form.setValue(`agents.${name}.enabled`, v, { shouldDirty: true })
                }
                aria-label={`Enable ${name} agent`}
              />
            </CardHeader>

            <div className={styles.fieldGrid}>
              <FormField label="Timeout (seconds)">
                {(fieldProps) => (
                  <Input
                    type="number"
                    min={10}
                    max={3600}
                    {...fieldProps}
                    {...form.register(`agents.${name}.timeout_seconds` as const, {
                      valueAsNumber: true,
                    })}
                  />
                )}
              </FormField>

              <FormField label="Autonomy level">
                {() => (
                  <Select
                    value={cfg?.autonomy ?? "low"}
                    onValueChange={(v) =>
                      form.setValue(
                        `agents.${name}.autonomy`,
                        v as "low" | "medium" | "high",
                        { shouldDirty: true },
                      )
                    }
                  >
                    <SelectItem value="low">Low</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                  </Select>
                )}
              </FormField>

              <FormField label="Allowed tools">
                {() => (
                  <div className={cn(styles.autoFitGrid, styles.autoFitGridCheckbox)}>
                    {ALL_TOOLS.map((tool) => {
                      const checked = selectedTools.has(tool);
                      return (
                        <Checkbox
                          key={tool}
                          checked={checked}
                          onCheckedChange={(v) => {
                            const next = new Set(selectedTools);
                            if (v) next.add(tool); else next.delete(tool);
                            form.setValue(
                              `agents.${name}.allowed_tools`,
                              Array.from(next),
                              { shouldDirty: true },
                            );
                          }}
                        >
                          {tool}
                        </Checkbox>
                      );
                    })}
                  </div>
                )}
              </FormField>
            </div>
          </Card>
        );
      })}
    </form>
  );
}
