import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import { Pill } from "../../primitives/Pill";
import { Switch } from "../../primitives/Switch";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../primitives/Tabs";
import { Skeleton } from "../../primitives/Skeleton";
import { JsonViewer } from "../../primitives/JsonViewer";
import {
  fetchCapabilityByName,
  fetchSkillDetail,
  setRequiresHumanGate,
  type Capability,
  type SkillDetail,
  type TransitionRow,
} from "../../api/skillSystem";
import StateTransitionForm from "./StateTransitionForm";
import styles from "./SkillDetailPanel.module.css";

interface Props {
  skillId: string | null;
  open: boolean;
  onClose: () => void;
  transitions: TransitionRow[];
  onRunsLink: (skillId: string) => void;
  onMutated: () => void;
}

export default function SkillDetailPanel({
  skillId,
  open,
  onClose,
  transitions,
  onRunsLink,
  onMutated,
}: Props) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [capability, setCapability] = useState<Capability | null>(null);
  const [loading, setLoading] = useState(false);
  const [gateBusy, setGateBusy] = useState(false);
  const [tab, setTab] = useState("overview");

  const refetch = useCallback(async () => {
    if (!skillId) return;
    setLoading(true);
    try {
      const d = await fetchSkillDetail(skillId);
      setDetail(d);
      try {
        const cap = await fetchCapabilityByName(d.capability_name);
        setCapability(cap);
      } catch {
        setCapability(null);
      }
    } catch {
      setDetail(null);
      setCapability(null);
    } finally {
      setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    if (open && skillId) {
      refetch();
      setTab("overview");
    } else {
      setDetail(null);
      setCapability(null);
    }
  }, [open, skillId, refetch]);

  const handleGateChange = async (value: boolean) => {
    if (!skillId) return;
    setGateBusy(true);
    try {
      await setRequiresHumanGate(skillId, value);
      toast.success(`Human gate ${value ? "enabled" : "disabled"}`);
      await refetch();
      onMutated();
    } catch {
      toast.error("Failed to update human gate flag");
    } finally {
      setGateBusy(false);
    }
  };

  const handleTransitionSuccess = async () => {
    await refetch();
    onMutated();
  };

  if (!open) return null;

  const schema = capability?.input_schema;
  const requiredFields = schema?.required ?? [];
  const properties = schema?.properties ?? {};
  const optionalFields = Object.keys(properties).filter(
    (k) => !requiredFields.includes(k),
  );

  return (
    <div className={styles.panel}>
      <button
        type="button"
        className={styles.closeBtn}
        onClick={onClose}
        aria-label="Close detail panel"
      >
        <X size={16} />
      </button>

      {loading && !detail ? (
        <Skeleton width="100%" height={120} />
      ) : !detail ? (
        <p className={styles.empty}>Skill not found.</p>
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="version">Version</TabsTrigger>
            <TabsTrigger value="transitions">Transitions</TabsTrigger>
          </TabsList>

          <TabsContent value="overview">
            <div className={styles.overviewGrid}>
              <section className={styles.section}>
                <h3 className={styles.sectionTitle}>Description</h3>
                <p className={styles.description}>
                  {capability?.description?.trim() || "No description available."}
                </p>

                {capability && (
                  <div className={styles.usageBlock}>
                    <h3 className={styles.sectionTitle}>Sample Usage</h3>
                    <code className={styles.usageCode}>
                      {generateSampleUsage(capability)}
                    </code>
                  </div>
                )}
              </section>

              <section className={styles.section}>
                <h3 className={styles.sectionTitle}>Required Inputs</h3>
                {requiredFields.length > 0 ? (
                  <dl className={styles.inputList}>
                    {requiredFields.map((field) => (
                      <div key={field} className={styles.inputItem}>
                        <dt className={styles.inputName}>{field}</dt>
                        <dd className={styles.inputDesc}>
                          <Pill variant="error">required</Pill>
                          <span className={styles.inputType}>
                            {formatType(properties[field]?.type)}
                          </span>
                          {properties[field]?.description && (
                            <span>{properties[field].description}</span>
                          )}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className={styles.muted}>None</p>
                )}

                <h3 className={styles.sectionTitle}>Optional Inputs</h3>
                {optionalFields.length > 0 ? (
                  <dl className={styles.inputList}>
                    {optionalFields.map((field) => (
                      <div key={field} className={styles.inputItem}>
                        <dt className={styles.inputName}>{field}</dt>
                        <dd className={styles.inputDesc}>
                          <Pill variant="muted">optional</Pill>
                          <span className={styles.inputType}>
                            {formatType(properties[field]?.type)}
                          </span>
                          {properties[field]?.description && (
                            <span>{properties[field].description}</span>
                          )}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className={styles.muted}>None</p>
                )}
              </section>

              <section className={styles.section}>
                <h3 className={styles.sectionTitle}>Skill Metadata</h3>
                <div className={styles.kv}>
                  <span className={styles.kvKey}>Capability</span>
                  <span className={styles.kvValue}>{detail.capability_name}</span>
                  <span className={styles.kvKey}>State</span>
                  <span className={styles.kvValue}>
                    <Pill variant="accent">{detail.state}</Pill>
                  </span>
                  <span className={styles.kvKey}>Trigger</span>
                  <span className={styles.kvValue}>
                    <Pill variant="muted">{capability?.trigger_type ?? "—"}</Pill>
                  </span>
                  <span className={styles.kvKey}>Human Gate</span>
                  <span className={styles.kvValue}>
                    <Switch
                      checked={detail.requires_human_gate}
                      onCheckedChange={handleGateChange}
                      disabled={gateBusy}
                      aria-label="Requires human gate"
                    />
                  </span>
                  <span className={styles.kvKey}>Agreement</span>
                  <span className={styles.kvValue}>
                    {detail.baseline_agreement !== null
                      ? detail.baseline_agreement.toFixed(3)
                      : "—"}
                  </span>
                  <span className={styles.kvKey}>Version</span>
                  <span className={styles.kvValue}>
                    {detail.current_version_id?.slice(0, 12) ?? "—"}
                  </span>
                  <span className={styles.kvKey}>Updated</span>
                  <span className={styles.kvValue}>
                    {detail.updated_at.replace("T", " ").slice(0, 19)}
                  </span>
                </div>
                <button
                  type="button"
                  className={styles.linkBtn}
                  onClick={() => onRunsLink(detail.id)}
                >
                  View runs for this skill
                </button>
              </section>
            </div>
          </TabsContent>

          <TabsContent value="version">
            {!detail.current_version ? (
              <p className={styles.muted}>No current version.</p>
            ) : (
              <div className={styles.versionGrid}>
                <section className={styles.section}>
                  <h3 className={styles.sectionTitle}>Version Info</h3>
                  <div className={styles.kv}>
                    <span className={styles.kvKey}>Number</span>
                    <span className={styles.kvValue}>
                      {detail.current_version.version_number}
                    </span>
                    <span className={styles.kvKey}>Created by</span>
                    <span className={styles.kvValue}>
                      {detail.current_version.created_by}
                    </span>
                    <span className={styles.kvKey}>Changelog</span>
                    <span className={styles.kvValue}>
                      {detail.current_version.changelog ?? "—"}
                    </span>
                  </div>
                </section>
                <section className={styles.section}>
                  <h3 className={styles.sectionTitle}>YAML Backbone</h3>
                  <JsonViewer value={detail.current_version.yaml_backbone} />
                </section>
                <section className={styles.section}>
                  <h3 className={styles.sectionTitle}>Step Content</h3>
                  <JsonViewer value={detail.current_version.step_content} />
                </section>
                <section className={styles.section}>
                  <h3 className={styles.sectionTitle}>Output Schemas</h3>
                  <JsonViewer value={detail.current_version.output_schemas} />
                </section>
              </div>
            )}
          </TabsContent>

          <TabsContent value="transitions">
            <StateTransitionForm
              skillId={detail.id}
              currentState={detail.state}
              transitions={transitions}
              onSuccess={handleTransitionSuccess}
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function formatType(t: string | string[] | undefined): string {
  if (!t) return "";
  if (Array.isArray(t)) return t.filter((v) => v !== "null").join(" | ");
  return t;
}

function generateSampleUsage(cap: Capability): string {
  const name = cap.name.replace(/_/g, " ");
  const schema = cap.input_schema;
  const required = schema?.required ?? [];
  const props = schema?.properties ?? {};

  const parts: string[] = [name];
  for (const field of required) {
    const prop = props[field];
    if (prop?.type === "string") {
      parts.push(`${field}="<value>"`);
    } else if (prop?.type === "array") {
      parts.push(`${field}=[...]`);
    } else {
      parts.push(`${field}=<${formatType(prop?.type) || "value"}>`);
    }
  }
  return parts.join(" ");
}
