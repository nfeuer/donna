import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "../../primitives/PageHeader";
import { Switch } from "../../primitives/Switch";
import { Segmented } from "../../primitives/Segmented";
import { Skeleton } from "../../primitives/Skeleton";
import RefreshButton from "../../components/RefreshButton";
import {
  fetchEscalationSettings,
  putEscalationSetting,
  putTaskTypeOverride,
  type EscalationSetting,
  type EscalationSettingsResponse,
  type TaskTypeOverride,
  type TaskTypeOverrideRow,
} from "../../api/escalationSettings";
import styles from "./EscalationSettings.module.css";

const SLIDER_KEY = "manual_escalation.budget_extension.max_daily_extension_usd";

const OVERRIDE_OPTIONS: { value: TaskTypeOverride; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "force_api", label: "Force-API" },
  { value: "force_manual", label: "Force-Manual" },
  { value: "disabled", label: "Disabled" },
];

interface ConflictDetail {
  current_value: boolean | number | string;
  current_updated_at: string;
  current_updated_by: string;
}

/**
 * Slice 23 — Escalation Settings page. Renders every dashboard-mutable
 * key from `config/manual_escalation.yaml` plus the per-task-type
 * override grid. All writes use optimistic locking on `updated_at`.
 *
 * Spec: docs/superpowers/specs/manual-escalation.md §6.3(a) / §10.7 row 1.
 */
export default function EscalationSettingsPage() {
  const [resp, setResp] = useState<EscalationSettingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Track which control is currently disabled because a write is in flight.
  const [pending, setPending] = useState<Record<string, boolean>>({});

  const doFetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchEscalationSettings();
      setResp(data);
    } catch (exc) {
      setError("Failed to load escalation settings.");
      // 500-class errors already toast via the axios interceptor.
      // eslint-disable-next-line no-console
      console.error("escalation_settings_fetch_failed", exc);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const settings = resp?.settings ?? [];
  const grid = resp?.task_type_overrides ?? [];
  const constraints = resp?.constraints;

  const isMasterDisabled = (() => {
    const master = settings.find(
      (s) => s.key === "manual_escalation.enabled",
    );
    return master ? master.value === false : false;
  })();

  const setPendingFor = useCallback(
    (key: string, value: boolean) => {
      setPending((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const applyConflict = useCallback(
    (key: string, conflict: ConflictDetail) => {
      // Surface the live state inline + toast so the user knows their
      // optimistic change was rejected and the UI now shows the truth.
      toast.warning("Setting changed elsewhere", {
        description: `${key}: now "${String(conflict.current_value)}" (by ${conflict.current_updated_by}). Showing latest.`,
      });
      setResp((prev) => {
        if (!prev) return prev;
        const next: EscalationSettingsResponse = {
          ...prev,
          settings: prev.settings.map((s) =>
            s.key === key
              ? {
                  ...s,
                  value: conflict.current_value as
                    | boolean
                    | number
                    | string,
                  updated_at: conflict.current_updated_at,
                  updated_by: conflict.current_updated_by,
                }
              : s,
          ),
          task_type_overrides: prev.task_type_overrides.map((row) =>
            row.key === key
              ? {
                  ...row,
                  value: conflict.current_value as TaskTypeOverride,
                  updated_at: conflict.current_updated_at,
                  updated_by: conflict.current_updated_by,
                }
              : row,
          ),
        };
        return next;
      });
    },
    [],
  );

  const writeSetting = useCallback(
    async (
      setting: EscalationSetting,
      newValue: boolean | number | string,
    ) => {
      setPendingFor(setting.key, true);
      try {
        const written = await putEscalationSetting(
          setting.key,
          newValue,
          setting.updated_at,
        );
        setResp((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            settings: prev.settings.map((s) =>
              s.key === setting.key
                ? {
                    ...s,
                    value: written.value,
                    updated_at: written.updated_at,
                    updated_by: written.updated_by,
                  }
                : s,
            ),
          };
        });
      } catch (exc: unknown) {
        const err = exc as {
          response?: {
            status?: number;
            data?: { detail?: ConflictDetail | { message?: string } };
          };
        };
        const status = err.response?.status;
        const detail = err.response?.data?.detail as
          | ConflictDetail
          | { message?: string }
          | undefined;
        if (status === 409 && detail && "current_updated_at" in detail) {
          applyConflict(setting.key, detail);
        } else if (status === 422) {
          const msg = (detail as { message?: string })?.message ?? "Invalid value.";
          toast.error("Cannot save", { description: msg });
          // Refetch so the UI snaps back.
          doFetch();
        } else {
          toast.error("Save failed", {
            description: `${setting.key}`,
          });
        }
      } finally {
        setPendingFor(setting.key, false);
      }
    },
    [applyConflict, doFetch, setPendingFor],
  );

  const writeOverride = useCallback(
    async (row: TaskTypeOverrideRow, newValue: TaskTypeOverride) => {
      setPendingFor(row.key, true);
      try {
        const written = await putTaskTypeOverride(
          row.task_type,
          newValue,
          row.updated_at,
        );
        setResp((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            task_type_overrides: prev.task_type_overrides.map((r) =>
              r.key === row.key
                ? {
                    ...r,
                    value: written.value,
                    updated_at: written.updated_at,
                    updated_by: written.updated_by,
                  }
                : r,
            ),
          };
        });
      } catch (exc: unknown) {
        const err = exc as {
          response?: {
            status?: number;
            data?: { detail?: ConflictDetail | { message?: string } };
          };
        };
        const status = err.response?.status;
        const detail = err.response?.data?.detail as
          | ConflictDetail
          | { message?: string }
          | undefined;
        if (status === 409 && detail && "current_updated_at" in detail) {
          applyConflict(row.key, detail);
        } else {
          toast.error("Save failed", {
            description: `${row.task_type} override`,
          });
        }
      } finally {
        setPendingFor(row.key, false);
      }
    },
    [applyConflict, setPendingFor],
  );

  return (
    <div>
      <PageHeader
        eyebrow="Operations"
        title="Escalation Settings"
        meta="Runtime overrides for manual handoff & budget extension"
        actions={
          <div className={styles.sectionHeader}>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      {error && <div className={styles.errorBanner}>{error}</div>}

      {loading && !resp && (
        <div className={styles.spinnerRow}>
          <Skeleton width="100%" height={400} />
        </div>
      )}

      {resp && (
        <>
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <div className={styles.sectionTitle}>Toggles</div>
                <div className={styles.sectionSubtitle}>
                  Resolution order: dashboard override → YAML default. Changes
                  take effect on the next escalation; in-flight escalations are
                  unaffected (spec §10.7).
                </div>
              </div>
            </div>

            <div className={styles.panel}>
              {settings
                .filter((s) => s.key !== SLIDER_KEY)
                .map((s) => (
                  <ToggleRow
                    key={s.key}
                    setting={s}
                    pending={!!pending[s.key]}
                    isMasterDisabled={
                      isMasterDisabled && s.key !== "manual_escalation.enabled"
                    }
                    onChange={(v) => writeSetting(s, v)}
                  />
                ))}

              {settings
                .filter((s) => s.key === SLIDER_KEY)
                .map((s) => (
                  <SliderRow
                    key={s.key}
                    setting={s}
                    pending={!!pending[s.key]}
                    capUsd={
                      constraints?.max_daily_extension_cap_usd ??
                      Number(s.value)
                    }
                    capBasis={constraints?.max_daily_extension_cap_basis}
                    isExtensionDisabled={(() => {
                      const ext = settings.find(
                        (x) =>
                          x.key === "manual_escalation.budget_extension.enabled",
                      );
                      return (
                        isMasterDisabled || (ext ? ext.value === false : false)
                      );
                    })()}
                    onChange={(v) => writeSetting(s, v)}
                  />
                ))}
            </div>

            {constraints && (
              <div className={styles.constraintNote}>
                <strong>hard_monthly_ceiling_usd</strong> (
                {constraints.max_daily_extension_cap_basis.hard_monthly_ceiling_usd.toFixed(2)}{" "}
                USD) is YAML-only and not editable here — defense in depth so a
                compromised dashboard session cannot authorise unlimited spend
                (spec §6.3 / §10.7 row 4). The slider above is capped at this
                ceiling divided by{" "}
                <strong>
                  {constraints.max_daily_extension_cap_basis.days_left_in_month}
                </strong>{" "}
                days remaining in the month.
              </div>
            )}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <div className={styles.sectionTitle}>
                  Per-task-type overrides
                </div>
                <div className={styles.sectionSubtitle}>
                  Only task types declaring a <code>manual_escalation</code>{" "}
                  block in <code>task_types.yaml</code> can be overridden.{" "}
                  <strong>Auto</strong> follows the global toggles above;{" "}
                  <strong>Force-API</strong> hides the manual button;{" "}
                  <strong>Force-Manual</strong> hides the api_extended button;{" "}
                  <strong>Disabled</strong> falls through to Pause / Cancel
                  only.
                </div>
              </div>
            </div>

            <div className={styles.panel}>
              {grid.length === 0 ? (
                <div className={styles.constraintNote}>
                  No task types declare a <code>manual_escalation</code> block.
                  Edit <code>config/task_types.yaml</code> to enable manual
                  handoffs, then refresh.
                </div>
              ) : (
                <div className={styles.tableScroll}>
                  <table className={styles.gridTable}>
                    <thead>
                      <tr>
                        <th>Task type</th>
                        <th>Manual mode</th>
                        <th>Override</th>
                        <th>Last changed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grid.map((row) => (
                        <OverrideGridRow
                          key={row.key}
                          row={row}
                          pending={!!pending[row.key]}
                          isMasterDisabled={isMasterDisabled}
                          onChange={(v) => writeOverride(row, v)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

interface ToggleRowProps {
  setting: EscalationSetting;
  pending: boolean;
  isMasterDisabled: boolean;
  onChange: (value: boolean) => void;
}

function ToggleRow({
  setting,
  pending,
  isMasterDisabled,
  onChange,
}: ToggleRowProps) {
  const checked = setting.value === true;
  const isOverride = setting.updated_at !== null;
  return (
    <div
      className={
        isMasterDisabled ? `${styles.row} ${styles.dimmedRow}` : styles.row
      }
    >
      <div className={styles.rowLabel}>
        <span className={styles.rowDescription}>{setting.description}</span>
        <span className={styles.rowKey}>{setting.key}</span>
        <span className={styles.rowMeta}>
          <span>YAML default: {String(setting.default)}</span>
          {isOverride ? (
            <span>
              Override by <strong>{setting.updated_by}</strong> ·{" "}
              {fmtTime(setting.updated_at)}
            </span>
          ) : (
            <span>No override (using YAML)</span>
          )}
        </span>
      </div>
      <div className={styles.rowControl}>
        <Switch
          checked={checked}
          disabled={pending || isMasterDisabled}
          onCheckedChange={onChange}
          aria-label={setting.key}
        />
      </div>
    </div>
  );
}

interface SliderRowProps {
  setting: EscalationSetting;
  pending: boolean;
  capUsd: number;
  capBasis?: { hard_monthly_ceiling_usd: number; days_left_in_month: number };
  isExtensionDisabled: boolean;
  onChange: (value: number) => void;
}

function SliderRow({
  setting,
  pending,
  capUsd,
  isExtensionDisabled,
  onChange,
}: SliderRowProps) {
  // Local value lets the user drag the slider smoothly; we only PUT on
  // change-end so a single drag isn't a 60-write storm.
  const [draft, setDraft] = useState<number>(Number(setting.value));
  useEffect(() => {
    setDraft(Number(setting.value));
  }, [setting.value, setting.updated_at]);

  const isOverride = setting.updated_at !== null;
  // Allow values up to the cap; if the YAML default exceeds today's cap
  // (e.g. month is mostly spent), pin the slider max to the cap and let
  // the user lower it but not raise above the ceiling.
  const maxSlider = Math.max(0, capUsd);

  return (
    <div
      className={
        isExtensionDisabled
          ? `${styles.sliderRow} ${styles.dimmedRow}`
          : styles.sliderRow
      }
    >
      <div className={styles.sliderHeader}>
        <div className={styles.rowLabel}>
          <span className={styles.rowDescription}>{setting.description}</span>
          <span className={styles.rowKey}>{setting.key}</span>
          <span className={styles.rowMeta}>
            <span>YAML default: ${Number(setting.default).toFixed(2)}</span>
            {isOverride ? (
              <span>
                Override by <strong>{setting.updated_by}</strong> ·{" "}
                {fmtTime(setting.updated_at)}
              </span>
            ) : (
              <span>No override (using YAML)</span>
            )}
          </span>
        </div>
        <div className={styles.sliderValue}>${draft.toFixed(2)}</div>
      </div>
      <div className={styles.sliderTrackWrap}>
        <input
          type="range"
          min={0}
          max={maxSlider}
          step={0.5}
          value={Math.min(draft, maxSlider)}
          disabled={pending || isExtensionDisabled}
          onChange={(e) => setDraft(Number(e.target.value))}
          onMouseUp={() => {
            if (draft !== Number(setting.value)) onChange(draft);
          }}
          onTouchEnd={() => {
            if (draft !== Number(setting.value)) onChange(draft);
          }}
          onKeyUp={(e) => {
            if (
              e.key === "ArrowLeft" ||
              e.key === "ArrowRight" ||
              e.key === "Home" ||
              e.key === "End"
            ) {
              if (draft !== Number(setting.value)) onChange(draft);
            }
          }}
          className={styles.slider}
          aria-label={setting.key}
        />
        <div className={styles.rowMeta}>
          <span>cap: ${capUsd.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

interface OverrideGridRowProps {
  row: TaskTypeOverrideRow;
  pending: boolean;
  isMasterDisabled: boolean;
  onChange: (value: TaskTypeOverride) => void;
}

function OverrideGridRow({
  row,
  pending,
  isMasterDisabled,
  onChange,
}: OverrideGridRowProps) {
  return (
    <tr className={isMasterDisabled ? styles.dimmedRow : undefined}>
      <td className={styles.taskTypeCell}>{row.task_type}</td>
      <td>
        <span className={styles.modePill}>{row.manual_mode}</span>
      </td>
      <td>
        <Segmented<TaskTypeOverride>
          value={row.value}
          onValueChange={(v) => {
            if (!pending && v !== row.value) onChange(v);
          }}
          options={OVERRIDE_OPTIONS}
          aria-label={`override-${row.task_type}`}
        />
      </td>
      <td className={styles.rowMeta}>
        {row.updated_at ? (
          <>
            {fmtTime(row.updated_at)}
            <br />
            by {row.updated_by}
          </>
        ) : (
          <span>—</span>
        )}
      </td>
    </tr>
  );
}

function fmtTime(value: string | null): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}
