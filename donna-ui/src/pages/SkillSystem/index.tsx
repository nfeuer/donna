import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../../primitives/Tabs";
import RefreshButton from "../../components/RefreshButton";
import SkillsTab from "./SkillsTab";
import SkillDrawer from "./SkillDrawer";
import CandidatesTab from "./CandidatesTab";
import CandidateDrawer from "./CandidateDrawer";
import DraftsTab from "./DraftsTab";
import RunsTab from "./RunsTab";
import RunDrawer from "./RunDrawer";
import AutomationsTab from "./AutomationsTab";
import AutomationDrawer from "./AutomationDrawer";
import {
  fetchSkillTransitions,
  type TransitionRow,
} from "../../api/skillSystem";

type TabKey = "skills" | "candidates" | "drafts" | "runs" | "automations";
const TAB_KEYS: TabKey[] = [
  "skills",
  "candidates",
  "drafts",
  "runs",
  "automations",
];

function isTabKey(v: string | null): v is TabKey {
  return v !== null && (TAB_KEYS as string[]).includes(v);
}

export default function SkillSystemPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: TabKey = isTabKey(tabParam) ? tabParam : "skills";
  const selectedId = searchParams.get("id");
  const skillFilter = searchParams.get("skill_id");

  const [refreshToken, setRefreshToken] = useState(0);
  const [transitions, setTransitions] = useState<TransitionRow[]>([]);
  const [automationMode, setAutomationMode] = useState<"edit" | "create">(
    "edit",
  );

  useEffect(() => {
    fetchSkillTransitions()
      .then((resp) => setTransitions(resp.transitions))
      .catch(() => setTransitions([]));
  }, []);

  const updateParams = useCallback(
    (updater: (sp: URLSearchParams) => void) => {
      const sp = new URLSearchParams(searchParams);
      updater(sp);
      setSearchParams(sp, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleTabChange = useCallback(
    (v: string) => {
      updateParams((sp) => {
        sp.set("tab", v);
        sp.delete("id");
      });
    },
    [updateParams],
  );

  const handleRowClick = useCallback(
    (id: string) => {
      updateParams((sp) => sp.set("id", id));
    },
    [updateParams],
  );

  const closeDrawer = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        updateParams((sp) => sp.delete("id"));
      }
    },
    [updateParams],
  );

  const handleRunsLink = useCallback(
    (skillId: string) => {
      updateParams((sp) => {
        sp.set("tab", "runs");
        sp.set("skill_id", skillId);
        sp.delete("id");
      });
    },
    [updateParams],
  );

  const clearSkillFilter = useCallback(() => {
    updateParams((sp) => sp.delete("skill_id"));
  }, [updateParams]);

  const handleAutomationNew = useCallback(() => {
    setAutomationMode("create");
    updateParams((sp) => sp.set("id", "__new__"));
  }, [updateParams]);

  const handleAutomationRowClick = useCallback(
    (id: string) => {
      setAutomationMode("edit");
      updateParams((sp) => sp.set("id", id));
    },
    [updateParams],
  );

  const handleRefresh = useCallback(async () => {
    setRefreshToken((n) => n + 1);
  }, []);

  const handleMutated = useCallback(() => {
    setRefreshToken((n) => n + 1);
  }, []);

  const drawerOpen = selectedId !== null;

  // SkillDrawer is used by both Skills and Drafts tabs.
  const skillDrawerOpen =
    drawerOpen && (tab === "skills" || tab === "drafts");
  const candidateDrawerOpen = drawerOpen && tab === "candidates";
  const runDrawerOpen = drawerOpen && tab === "runs";
  const automationDrawerOpen = drawerOpen && tab === "automations";

  const automationDrawerId = useMemo(() => {
    if (!automationDrawerOpen) return null;
    return automationMode === "create" ? null : selectedId;
  }, [automationDrawerOpen, automationMode, selectedId]);

  return (
    <div>
      <PageHeader
        eyebrow="Infrastructure"
        title="Skill System"
        actions={<RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />}
      />
      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          <TabsTrigger value="drafts">Drafts</TabsTrigger>
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="automations">Automations</TabsTrigger>
        </TabsList>

        <TabsContent value="skills">
          <SkillsTab
            selectedId={tab === "skills" ? selectedId : null}
            onRowClick={handleRowClick}
            refreshToken={refreshToken}
          />
        </TabsContent>
        <TabsContent value="candidates">
          <CandidatesTab
            selectedId={tab === "candidates" ? selectedId : null}
            onRowClick={handleRowClick}
            refreshToken={refreshToken}
          />
        </TabsContent>
        <TabsContent value="drafts">
          <DraftsTab
            selectedId={tab === "drafts" ? selectedId : null}
            onRowClick={handleRowClick}
            refreshToken={refreshToken}
          />
        </TabsContent>
        <TabsContent value="runs">
          <RunsTab
            selectedId={tab === "runs" ? selectedId : null}
            onRowClick={handleRowClick}
            skillIdFilter={skillFilter}
            onClearSkillFilter={clearSkillFilter}
            refreshToken={refreshToken}
          />
        </TabsContent>
        <TabsContent value="automations">
          <AutomationsTab
            selectedId={tab === "automations" ? selectedId : null}
            onRowClick={handleAutomationRowClick}
            onNew={handleAutomationNew}
            refreshToken={refreshToken}
          />
        </TabsContent>
      </Tabs>

      <SkillDrawer
        skillId={skillDrawerOpen ? selectedId : null}
        open={skillDrawerOpen}
        onOpenChange={closeDrawer}
        transitions={transitions}
        onRunsLink={handleRunsLink}
        onMutated={handleMutated}
      />
      <CandidateDrawer
        candidateId={candidateDrawerOpen ? selectedId : null}
        open={candidateDrawerOpen}
        onOpenChange={closeDrawer}
        onMutated={handleMutated}
      />
      <RunDrawer
        runId={runDrawerOpen ? selectedId : null}
        open={runDrawerOpen}
        onOpenChange={closeDrawer}
        onMutated={handleMutated}
      />
      <AutomationDrawer
        automationId={automationDrawerId}
        mode={automationMode}
        open={automationDrawerOpen}
        onOpenChange={closeDrawer}
        onMutated={handleMutated}
      />
    </div>
  );
}
