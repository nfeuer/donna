import { useState, useCallback, useEffect } from "react";
import { PageHeader, Select, SelectItem, EmptyState } from "../../primitives";
import RefreshButton from "../../components/RefreshButton";
import RulesTable from "./RulesTable";
import CorrectionsTable from "./CorrectionsTable";
import RuleDetailDrawer from "./RuleDetailDrawer";
import {
  fetchPreferenceRules,
  fetchCorrections,
  fetchPreferenceStats,
  type PreferenceRule,
  type CorrectionEntry,
  type PreferenceStats,
} from "../../api/preferences";
import styles from "./Preferences.module.css";

const RULE_TYPE_OPTIONS = [
  { value: "scheduling", label: "Scheduling" },
  { value: "priority", label: "Priority" },
  { value: "domain", label: "Domain" },
  { value: "formatting", label: "Formatting" },
  { value: "delegation", label: "Delegation" },
];

const ENABLED_OPTIONS = [
  { value: "true", label: "Enabled" },
  { value: "false", label: "Disabled" },
];

export default function PreferencesPage() {
  // Filters
  const [ruleType, setRuleType] = useState("");
  const [enabledFilter, setEnabledFilter] = useState("");
  const [corrField, setCorrField] = useState("");
  const [corrTaskType, setCorrTaskType] = useState("");

  // Data
  const [rules, setRules] = useState<PreferenceRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [corrTotal, setCorrTotal] = useState(0);
  const [corrLoading, setCorrLoading] = useState(false);
  const [stats, setStats] = useState<PreferenceStats | null>(null);

  // Drawer
  const [selectedRule, setSelectedRule] = useState<PreferenceRule | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setRulesLoading(true);
    setCorrLoading(true);
    try {
      const enabledVal =
        enabledFilter === "true" ? true : enabledFilter === "false" ? false : undefined;

      const [rulesResp, corrResp, statsResp] = await Promise.all([
        fetchPreferenceRules({
          rule_type: ruleType || undefined,
          enabled: enabledVal,
        }),
        fetchCorrections({
          field: corrField || undefined,
          task_type: corrTaskType || undefined,
        }),
        fetchPreferenceStats(),
      ]);
      setRules(rulesResp.rules);
      setCorrections(corrResp.corrections);
      setCorrTotal(corrResp.total);
      setStats(statsResp);
    } catch {
      setRules([]);
      setCorrections([]);
      setCorrTotal(0);
      setStats(null);
    } finally {
      setRulesLoading(false);
      setCorrLoading(false);
    }
  }, [ruleType, enabledFilter, corrField, corrTaskType]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRuleClick = (rule: PreferenceRule) => {
    setSelectedRule(rule);
    setDrawerOpen(true);
  };

  const hasRules = rules.length > 0 || rulesLoading;

  return (
    <div>
      <PageHeader
        title="Preferences"
        meta="Learned rules & corrections"
        actions={
          <div className={styles.filters}>
            <Select
              value={ruleType}
              onValueChange={setRuleType}
              placeholder="All rule types"
              aria-label="Filter by rule type"
            >
              {RULE_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <Select
              value={enabledFilter}
              onValueChange={setEnabledFilter}
              placeholder="All states"
              aria-label="Filter by enabled state"
            >
              {ENABLED_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      {!hasRules ? (
        <EmptyState
          title="No rules learned yet."
          body="Donna picks these up as you correct her."
        />
      ) : (
        <>
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                Learned Rules
                <span className={styles.sectionCount}>{rules.length}</span>
              </h2>
            </div>
            <RulesTable
              rules={rules}
              loading={rulesLoading}
              onRuleClick={handleRuleClick}
              onRuleToggled={doFetch}
            />
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                Corrections
                <span className={styles.sectionCount}>{corrTotal}</span>
              </h2>
              <div className={styles.inlineFilters}>
                <Select
                  value={corrField}
                  onValueChange={(v) => setCorrField(v)}
                  placeholder="All fields"
                  aria-label="Filter corrections by field"
                >
                  {(stats?.top_fields ?? []).map((f) => (
                    <SelectItem key={f.field} value={f.field}>
                      {f.field} ({f.count})
                    </SelectItem>
                  ))}
                </Select>
                <Select
                  value={corrTaskType}
                  onValueChange={(v) => setCorrTaskType(v)}
                  placeholder="All task types"
                  aria-label="Filter corrections by task type"
                >
                  <SelectItem value="parse_task">parse_task</SelectItem>
                  <SelectItem value="classify_priority">classify_priority</SelectItem>
                  <SelectItem value="extract_deadline">extract_deadline</SelectItem>
                </Select>
              </div>
            </div>
            <CorrectionsTable
              corrections={corrections}
              loading={corrLoading}
            />
          </section>
        </>
      )}

      <RuleDetailDrawer
        rule={selectedRule}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
