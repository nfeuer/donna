import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Drawer } from "../../primitives/Drawer";
import { Button } from "../../primitives/Button";
import { Input, Textarea } from "../../primitives/Input";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import { Skeleton } from "../../primitives/Skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../primitives/Tabs";
import { JsonViewer } from "../../primitives/JsonViewer";
import {
  createAutomation,
  deleteAutomation,
  fetchAutomationDetail,
  fetchAutomationRuns,
  pauseAutomation,
  resumeAutomation,
  runAutomationNow,
  updateAutomation,
  type Automation,
  type AutomationRun,
  type CreateAutomationBody,
  type UpdateAutomationBody,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  automationId: string | null;
  mode: "edit" | "create";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMutated: () => void;
}

interface FormState {
  name: string;
  description: string;
  capability_name: string;
  inputs_text: string;
  trigger_type: string;
  schedule: string;
  alert_channels_text: string; // comma-separated
  alert_conditions_text: string;
  max_cost_per_run_usd: string;
  min_interval_seconds: string;
  user_id: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  capability_name: "",
  inputs_text: "{}",
  trigger_type: "on_schedule",
  schedule: "",
  alert_channels_text: "",
  alert_conditions_text: "{}",
  max_cost_per_run_usd: "",
  min_interval_seconds: "300",
  user_id: "nick",
};

function statusVariant(s: string): PillVariant {
  if (s === "active") return "success";
  if (s === "paused") return "warning";
  if (s === "deleted") return "muted";
  return "accent";
}

function automationToForm(a: Automation): FormState {
  return {
    name: a.name,
    description: a.description ?? "",
    capability_name: a.capability_name,
    inputs_text: JSON.stringify(a.inputs ?? {}, null, 2),
    trigger_type: a.trigger_type,
    schedule: a.schedule ?? "",
    alert_channels_text: (a.alert_channels ?? []).join(", "),
    alert_conditions_text: JSON.stringify(a.alert_conditions ?? {}, null, 2),
    max_cost_per_run_usd:
      a.max_cost_per_run_usd !== null ? String(a.max_cost_per_run_usd) : "",
    min_interval_seconds: String(a.min_interval_seconds),
    user_id: a.user_id,
  };
}

function parseJsonSafe(
  text: string,
  fallback: unknown,
): { ok: true; value: unknown } | { ok: false; error: string } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: fallback };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

export default function AutomationDrawer({
  automationId,
  mode,
  open,
  onOpenChange,
  onMutated,
}: Props) {
  const [automation, setAutomation] = useState<Automation | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("form");

  const refetch = useCallback(async () => {
    if (mode !== "edit" || !automationId) return;
    setLoading(true);
    try {
      const [detail, runsResp] = await Promise.all([
        fetchAutomationDetail(automationId),
        fetchAutomationRuns(automationId, 25).catch(() => ({
          runs: [],
          count: 0,
        })),
      ]);
      setAutomation(detail);
      setForm(automationToForm(detail));
      setRuns(runsResp.runs);
    } catch {
      setAutomation(null);
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, [mode, automationId]);

  useEffect(() => {
    if (!open) {
      setAutomation(null);
      setError(null);
      setTab("form");
      return;
    }
    if (mode === "create") {
      setForm(EMPTY_FORM);
      setAutomation(null);
      setRuns([]);
      setError(null);
      setTab("form");
    } else {
      refetch();
    }
  }, [open, mode, automationId, refetch]);

  const updateForm = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const buildBody = ():
    | { ok: true; create: CreateAutomationBody; update: UpdateAutomationBody }
    | { ok: false; error: string } => {
    if (!form.name.trim()) return { ok: false, error: "Name is required." };
    if (!form.capability_name.trim())
      return { ok: false, error: "Capability is required." };

    const inputs = parseJsonSafe(form.inputs_text, {});
    if (!inputs.ok) return { ok: false, error: `inputs JSON: ${inputs.error}` };
    const alertConditions = parseJsonSafe(form.alert_conditions_text, {});
    if (!alertConditions.ok)
      return {
        ok: false,
        error: `alert_conditions JSON: ${alertConditions.error}`,
      };

    const alertChannels = form.alert_channels_text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const maxCost =
      form.max_cost_per_run_usd.trim() === ""
        ? null
        : Number(form.max_cost_per_run_usd);
    if (maxCost !== null && Number.isNaN(maxCost))
      return { ok: false, error: "max_cost_per_run_usd must be a number." };

    const minInterval = Number(form.min_interval_seconds);
    if (Number.isNaN(minInterval) || minInterval < 0)
      return {
        ok: false,
        error: "min_interval_seconds must be a non-negative number.",
      };

    if (form.trigger_type === "on_schedule" && !form.schedule.trim())
      return {
        ok: false,
        error: "Schedule (cron) is required when trigger_type is on_schedule.",
      };

    const create: CreateAutomationBody = {
      user_id: form.user_id || "nick",
      name: form.name.trim(),
      description: form.description.trim() || null,
      capability_name: form.capability_name.trim(),
      inputs: inputs.value as Record<string, unknown>,
      trigger_type: form.trigger_type,
      schedule: form.schedule.trim() || null,
      alert_conditions: alertConditions.value as Record<string, unknown>,
      alert_channels: alertChannels,
      max_cost_per_run_usd: maxCost,
      min_interval_seconds: minInterval,
      created_via: "dashboard",
    };
    const update: UpdateAutomationBody = {
      name: create.name,
      description: create.description,
      inputs: create.inputs,
      schedule: create.schedule,
      alert_conditions: create.alert_conditions,
      alert_channels: create.alert_channels,
      max_cost_per_run_usd: create.max_cost_per_run_usd,
      min_interval_seconds: create.min_interval_seconds,
    };
    return { ok: true, create, update };
  };

  const handleSave = async () => {
    setError(null);
    const built = buildBody();
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setBusy(true);
    try {
      if (mode === "create") {
        await createAutomation(built.create);
        toast.success("Automation created");
      } else if (automationId) {
        await updateAutomation(automationId, built.update);
        toast.success("Automation updated");
      }
      onMutated();
      onOpenChange(false);
    } catch (err: unknown) {
      const msg =
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail
          ? (err as { response: { data: { detail: string } } }).response.data
              .detail
          : "Save failed";
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const wrapMutation = (fn: () => Promise<unknown>, successMsg: string) =>
    async () => {
      if (!automationId) return;
      setBusy(true);
      try {
        await fn();
        toast.success(successMsg);
        await refetch();
        onMutated();
      } catch {
        toast.error(successMsg.replace(/ed$/, " failed"));
      } finally {
        setBusy(false);
      }
    };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={
        mode === "create"
          ? "New Automation"
          : automation
            ? automation.name
            : "Automation"
      }
    >
      <div className={styles.drawerBody}>
        {loading && !automation && mode === "edit" ? (
          <Skeleton width="100%" height={240} />
        ) : (
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="form">
                {mode === "create" ? "Create" : "Edit"}
              </TabsTrigger>
              {mode === "edit" && <TabsTrigger value="runs">Runs</TabsTrigger>}
            </TabsList>
            <TabsContent value="form">
              <div className={styles.drawerBody} style={{ gap: "var(--space-3)" }}>
                {mode === "edit" && automation && (
                  <div className={styles.kv}>
                    <span className={styles.kvKey}>Status</span>
                    <span className={styles.kvValue}>
                      <Pill variant={statusVariant(automation.status)}>
                        {automation.status}
                      </Pill>
                    </span>
                    <span className={styles.kvKey}>Next run</span>
                    <span className={styles.kvValue}>
                      {automation.next_run_at ?? "—"}
                    </span>
                    <span className={styles.kvKey}>Runs · failures</span>
                    <span className={styles.kvValue}>
                      {automation.run_count} · {automation.failure_count}
                    </span>
                  </div>
                )}
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>Name</label>
                  <Input
                    value={form.name}
                    onChange={(e) => updateForm("name", e.target.value)}
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>Description</label>
                  <Textarea
                    value={form.description}
                    rows={2}
                    onChange={(e) => updateForm("description", e.target.value)}
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Capability name
                  </label>
                  <Input
                    value={form.capability_name}
                    onChange={(e) =>
                      updateForm("capability_name", e.target.value)
                    }
                    disabled={mode === "edit"}
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>Trigger type</label>
                  <Select
                    value={form.trigger_type}
                    onValueChange={(v) => updateForm("trigger_type", v)}
                    aria-label="Trigger type"
                  >
                    <SelectItem value="on_schedule">on_schedule</SelectItem>
                    <SelectItem value="on_manual">on_manual</SelectItem>
                  </Select>
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Schedule (cron, 5-field)
                  </label>
                  <Input
                    value={form.schedule}
                    onChange={(e) => updateForm("schedule", e.target.value)}
                    placeholder="0 8 * * *"
                    disabled={form.trigger_type !== "on_schedule"}
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Inputs (JSON)
                  </label>
                  <Textarea
                    value={form.inputs_text}
                    rows={4}
                    onChange={(e) => updateForm("inputs_text", e.target.value)}
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Alert conditions (JSON)
                  </label>
                  <Textarea
                    value={form.alert_conditions_text}
                    rows={3}
                    onChange={(e) =>
                      updateForm("alert_conditions_text", e.target.value)
                    }
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Alert channels (comma-separated)
                  </label>
                  <Input
                    value={form.alert_channels_text}
                    onChange={(e) =>
                      updateForm("alert_channels_text", e.target.value)
                    }
                    placeholder="discord, sms"
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Max cost per run (USD, optional)
                  </label>
                  <Input
                    value={form.max_cost_per_run_usd}
                    onChange={(e) =>
                      updateForm("max_cost_per_run_usd", e.target.value)
                    }
                    placeholder="0.10"
                  />
                </div>
                <div className={styles.formRow}>
                  <label className={styles.formLabel}>
                    Min interval seconds
                  </label>
                  <Input
                    value={form.min_interval_seconds}
                    onChange={(e) =>
                      updateForm("min_interval_seconds", e.target.value)
                    }
                  />
                </div>
                {mode === "create" && (
                  <div className={styles.formRow}>
                    <label className={styles.formLabel}>User id</label>
                    <Input
                      value={form.user_id}
                      onChange={(e) => updateForm("user_id", e.target.value)}
                    />
                  </div>
                )}
                {error && <div className={styles.formError}>{error}</div>}
                <div className={styles.actions}>
                  <Button onClick={handleSave} disabled={busy}>
                    {mode === "create" ? "Create" : "Save changes"}
                  </Button>
                  {mode === "edit" && automation && (
                    <>
                      {automation.status === "active" ? (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={wrapMutation(
                            () => pauseAutomation(automation.id),
                            "Paused",
                          )}
                        >
                          Pause
                        </Button>
                      ) : automation.status === "paused" ? (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={wrapMutation(
                            () => resumeAutomation(automation.id),
                            "Resumed",
                          )}
                        >
                          Resume
                        </Button>
                      ) : null}
                      <Button
                        variant="ghost"
                        disabled={busy || automation.status !== "active"}
                        onClick={wrapMutation(
                          () => runAutomationNow(automation.id),
                          "Scheduled",
                        )}
                      >
                        Run now
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy || automation.status === "deleted"}
                        onClick={wrapMutation(
                          () => deleteAutomation(automation.id),
                          "Deleted",
                        )}
                      >
                        Delete
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </TabsContent>
            {mode === "edit" && (
              <TabsContent value="runs">
                <div className={styles.drawerBody}>
                  {runs.length === 0 ? (
                    <p className={styles.kvValue}>No runs recorded.</p>
                  ) : (
                    runs.map((r) => (
                      <div key={r.id} className={styles.step}>
                        <div className={styles.stepHeader}>
                          <span className={styles.stepName}>
                            {r.started_at}
                          </span>
                          <Pill variant={statusVariant(r.status)}>
                            {r.status}
                          </Pill>
                        </div>
                        <div className={styles.inlineMeta}>
                          <span>path: {r.execution_path}</span>
                          {r.cost_usd !== null && (
                            <span>cost: ${r.cost_usd.toFixed(4)}</span>
                          )}
                          {r.error && <span>error: {r.error}</span>}
                        </div>
                        {r.output && (
                          <details>
                            <summary
                              style={{
                                cursor: "pointer",
                                fontSize: "var(--text-small)",
                                color: "var(--color-text-muted)",
                              }}
                            >
                              output
                            </summary>
                            <JsonViewer value={r.output} />
                          </details>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </TabsContent>
            )}
          </Tabs>
        )}
      </div>
    </Drawer>
  );
}
