import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Drawer } from "../../primitives/Drawer";
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
  fetchSkillDetail,
  setRequiresHumanGate,
  type SkillDetail,
  type TransitionRow,
} from "../../api/skillSystem";
import StateTransitionForm from "./StateTransitionForm";
import styles from "./SkillSystem.module.css";

interface Props {
  skillId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  transitions: TransitionRow[];
  onRunsLink: (skillId: string) => void;
  onMutated: () => void;
}

export default function SkillDrawer({
  skillId,
  open,
  onOpenChange,
  transitions,
  onRunsLink,
  onMutated,
}: Props) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [gateBusy, setGateBusy] = useState(false);

  const refetch = useCallback(async () => {
    if (!skillId) return;
    setLoading(true);
    try {
      const d = await fetchSkillDetail(skillId);
      setDetail(d);
    } catch {
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    if (open && skillId) {
      refetch();
    } else {
      setDetail(null);
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

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={detail ? detail.capability_name : "Skill"}
    >
      <div className={styles.drawerBody}>
        {loading && !detail ? (
          <Skeleton width="100%" height={200} />
        ) : !detail ? (
          <p className={styles.kvValue}>Skill not found.</p>
        ) : (
          <DrawerTabs
            detail={detail}
            gateBusy={gateBusy}
            onGateChange={handleGateChange}
            onTransitionSuccess={handleTransitionSuccess}
            onRunsLink={onRunsLink}
            transitions={transitions}
          />
        )}
      </div>
    </Drawer>
  );
}

function DrawerTabs({
  detail,
  gateBusy,
  onGateChange,
  onTransitionSuccess,
  onRunsLink,
  transitions,
}: {
  detail: SkillDetail;
  gateBusy: boolean;
  onGateChange: (value: boolean) => void;
  onTransitionSuccess: () => Promise<void>;
  onRunsLink: (skillId: string) => void;
  transitions: TransitionRow[];
}) {
  const [tab, setTab] = useState("overview");
  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList>
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="version">Version</TabsTrigger>
        <TabsTrigger value="transitions">Transitions</TabsTrigger>
      </TabsList>
      <TabsContent value="overview">
        <div className={styles.drawerBody}>
          <section className={styles.drawerSection}>
            <h3 className={styles.drawerSectionTitle}>Skill</h3>
            <div className={styles.kv}>
              <span className={styles.kvKey}>Capability</span>
              <span className={styles.kvValue}>{detail.capability_name}</span>
              <span className={styles.kvKey}>State</span>
              <span className={styles.kvValue}>
                <Pill variant="accent">{detail.state}</Pill>
              </span>
              <span className={styles.kvKey}>Baseline agreement</span>
              <span className={styles.kvValue}>
                {detail.baseline_agreement !== null
                  ? detail.baseline_agreement.toFixed(3)
                  : "—"}
              </span>
              <span className={styles.kvKey}>Version id</span>
              <span className={styles.kvValue}>
                {detail.current_version_id ?? "—"}
              </span>
              <span className={styles.kvKey}>Created</span>
              <span className={styles.kvValue}>{detail.created_at}</span>
              <span className={styles.kvKey}>Updated</span>
              <span className={styles.kvValue}>{detail.updated_at}</span>
            </div>
          </section>
          <section className={styles.drawerSection}>
            <h3 className={styles.drawerSectionTitle}>Flags</h3>
            <div className={styles.kv}>
              <span className={styles.kvKey}>requires_human_gate</span>
              <span className={styles.kvValue}>
                <Switch
                  checked={detail.requires_human_gate}
                  onCheckedChange={onGateChange}
                  disabled={gateBusy}
                  aria-label="Requires human gate"
                />
              </span>
            </div>
          </section>
          <section className={styles.drawerSection}>
            <h3 className={styles.drawerSectionTitle}>Links</h3>
            <div className={styles.actions}>
              <button
                type="button"
                onClick={() => onRunsLink(detail.id)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--color-border-subtle)",
                  color: "var(--color-text-primary)",
                  padding: "6px 10px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  fontSize: "var(--text-small)",
                }}
              >
                View runs for this skill
              </button>
            </div>
          </section>
        </div>
      </TabsContent>
      <TabsContent value="version">
        <div className={styles.drawerBody}>
          {!detail.current_version ? (
            <p className={styles.kvValue}>No current version.</p>
          ) : (
            <>
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>Version</h3>
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
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>YAML backbone</h3>
                <JsonViewer value={detail.current_version.yaml_backbone} />
              </section>
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>Step content</h3>
                <JsonViewer value={detail.current_version.step_content} />
              </section>
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>Output schemas</h3>
                <JsonViewer value={detail.current_version.output_schemas} />
              </section>
            </>
          )}
        </div>
      </TabsContent>
      <TabsContent value="transitions">
        <StateTransitionForm
          skillId={detail.id}
          currentState={detail.state}
          transitions={transitions}
          onSuccess={onTransitionSuccess}
        />
      </TabsContent>
    </Tabs>
  );
}
