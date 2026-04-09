import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Switch } from "../../../primitives/Switch";
import { DataTable } from "../../../primitives/DataTable";
import { modelsSchema, type ModelsConfig } from "../schemas";
import styles from "./Forms.module.css";
import { cn } from "../../../lib/cn";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

export default function ModelsForm({ data, onChange }: Props) {
  const form = useForm<ModelsConfig>({
    values: data as ModelsConfig,
    resolver: zodResolver(modelsSchema) as any,
    mode: "onChange",
  });

  // Sync form state -> parent on every valid change.
  useEffect(() => {
    const sub = form.watch((values) => {
      onChange(values as Record<string, any>);
    });
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const models = form.watch("models") ?? {};
  const routing = form.watch("routing") ?? {};

  const modelRows = Object.entries(models).map(([alias, cfg]) => ({
    alias,
    provider: cfg?.provider ?? "",
    model: cfg?.model ?? "",
  }));

  const routingRows = Object.entries(routing).map(([taskType, cfg]) => ({
    task_type: taskType,
    model: cfg?.model ?? "",
    fallback: cfg?.fallback ?? "",
    shadow: cfg?.shadow ?? "",
    confidence_threshold: cfg?.confidence_threshold,
  }));

  const topError = form.formState.errors.root?.message;

  return (
    <form className={styles.stack} onSubmit={(e) => e.preventDefault()}>
      {topError && (
        <div role="alert" style={{ color: "var(--color-error)" }}>
          Schema error: {String(topError)}
        </div>
      )}

      <Card>
        <CardHeader><CardTitle>Model definitions</CardTitle></CardHeader>
        <DataTable
          data={modelRows}
          getRowId={(row) => row.alias}
          columns={[
            { header: "Alias", accessorKey: "alias" },
            {
              header: "Provider",
              accessorKey: "provider",
              cell: ({ row }) => (
                <Input
                  aria-label={`${row.original.alias} provider`}
                  {...form.register(`models.${row.original.alias}.provider` as const)}
                />
              ),
            },
            {
              header: "Model",
              accessorKey: "model",
              cell: ({ row }) => (
                <Input
                  aria-label={`${row.original.alias} model`}
                  {...form.register(`models.${row.original.alias}.model` as const)}
                />
              ),
            },
          ]}
        />
      </Card>

      <Card>
        <CardHeader><CardTitle>Routing table</CardTitle></CardHeader>
        <DataTable
          data={routingRows}
          getRowId={(row) => row.task_type}
          columns={[
            { header: "Task type", accessorKey: "task_type" },
            {
              header: "Model",
              accessorKey: "model",
              cell: ({ row }) => (
                <Input
                  aria-label={`${row.original.task_type} model`}
                  {...form.register(`routing.${row.original.task_type}.model` as const)}
                />
              ),
            },
            {
              header: "Fallback",
              accessorKey: "fallback",
              cell: ({ row }) => (
                <Input
                  aria-label={`${row.original.task_type} fallback`}
                  {...form.register(`routing.${row.original.task_type}.fallback` as const)}
                />
              ),
            },
            {
              header: "Shadow",
              accessorKey: "shadow",
              cell: ({ row }) => (
                <Input
                  aria-label={`${row.original.task_type} shadow`}
                  {...form.register(`routing.${row.original.task_type}.shadow` as const)}
                />
              ),
            },
            {
              header: "Threshold",
              accessorKey: "confidence_threshold",
              cell: ({ row }) => (
                <Input
                  type="number"
                  step={0.1}
                  min={0}
                  max={1}
                  aria-label={`${row.original.task_type} confidence threshold`}
                  {...form.register(
                    `routing.${row.original.task_type}.confidence_threshold` as const,
                    { valueAsNumber: true },
                  )}
                />
              ),
            },
          ]}
        />
      </Card>

      <Card>
        <CardHeader><CardTitle>Cost tracking</CardTitle></CardHeader>
        <div className={cn(styles.autoFitGrid, styles.autoFitGridNarrow)}>
          <FormField label="Monthly budget ($)">
            {(fieldProps) => (
              <Input type="number" step={10} min={0} {...fieldProps} {...form.register("cost.monthly_budget_usd", { valueAsNumber: true })} />
            )}
          </FormField>
          <FormField label="Daily pause ($)">
            {(fieldProps) => (
              <Input type="number" step={5} min={0} {...fieldProps} {...form.register("cost.daily_pause_threshold_usd", { valueAsNumber: true })} />
            )}
          </FormField>
          <FormField label="Task approval ($)">
            {(fieldProps) => (
              <Input type="number" step={1} min={0} {...fieldProps} {...form.register("cost.task_approval_threshold_usd", { valueAsNumber: true })} />
            )}
          </FormField>
          <FormField label="Warning %">
            {(fieldProps) => (
              <Input type="number" step={0.05} min={0} max={1} {...fieldProps} {...form.register("cost.monthly_warning_pct", { valueAsNumber: true })} />
            )}
          </FormField>
        </div>
      </Card>

      <Card>
        <CardHeader><CardTitle>Quality monitoring</CardTitle></CardHeader>
        <div className={styles.inlineRow}>
          <FormField label="Enabled">
            {() => (
              <Switch
                checked={!!form.watch("quality_monitoring.enabled")}
                onCheckedChange={(v) => form.setValue("quality_monitoring.enabled", v, { shouldDirty: true })}
              />
            )}
          </FormField>
          <FormField label="Spot check rate">
            {(fieldProps) => (
              <Input type="number" step={0.01} min={0} max={1} {...fieldProps} {...form.register("quality_monitoring.spot_check_rate", { valueAsNumber: true })} />
            )}
          </FormField>
          <FormField label="Flag threshold">
            {(fieldProps) => (
              <Input type="number" step={0.1} min={0} max={1} {...fieldProps} {...form.register("quality_monitoring.flag_threshold", { valueAsNumber: true })} />
            )}
          </FormField>
        </div>
      </Card>
    </form>
  );
}
