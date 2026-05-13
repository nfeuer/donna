import { useState, useCallback, useEffect, useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { PageHeader } from "../../primitives/PageHeader";
import { Stat } from "../../primitives/Stat";
import { Pill } from "../../primitives/Pill";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { DataTable } from "../../primitives/DataTable";
import { Select, SelectItem } from "../../primitives/Select";
import RefreshButton from "../../components/RefreshButton";
import NoteViewer from "./NoteViewer";
import CommitHistory from "./CommitHistory";
import {
  fetchNotes,
  fetchNote,
  fetchVaultStatus,
  fetchVaultHistory,
  type VaultNoteSummary,
  type VaultNote,
  type VaultStatus,
  type VaultCommit,
} from "../../api/vault";
import styles from "./Vault.module.css";

export default function VaultPage() {
  const [status, setStatus] = useState<VaultStatus | null>(null);
  const [notes, setNotes] = useState<VaultNoteSummary[]>([]);
  const [commits, setCommits] = useState<VaultCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [commitsLoading, setCommitsLoading] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState("notes");

  const [selectedNote, setSelectedNote] = useState<VaultNote | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const [statusResp, notesResp] = await Promise.all([
        fetchVaultStatus(),
        fetchNotes({ folder: folder || undefined }),
      ]);
      setStatus(statusResp);
      setNotes(notesResp.notes);
    } catch {
      setStatus(null);
      setNotes([]);
    } finally {
      setLoading(false);
    }
  }, [folder]);

  const loadCommits = useCallback(async () => {
    setCommitsLoading(true);
    try {
      const resp = await fetchVaultHistory();
      setCommits(resp.commits);
    } catch {
      setCommits([]);
    } finally {
      setCommitsLoading(false);
    }
  }, []);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRowClick = useCallback(async (row: VaultNoteSummary) => {
    try {
      const note = await fetchNote(row.path);
      setSelectedNote(note);
      setDrawerOpen(true);
    } catch {
      // toast via interceptor
    }
  }, []);

  const folders = useMemo(() => {
    const set = new Set<string>();
    notes.forEach((n) => {
      const parts = n.path.split("/");
      if (parts.length > 1) set.add(parts[0]);
    });
    return Array.from(set).sort();
  }, [notes]);

  const filteredNotes = useMemo(() => {
    if (!search) return notes;
    const q = search.toLowerCase();
    return notes.filter((n) => n.path.toLowerCase().includes(q));
  }, [notes, search]);

  const noteColumns = useMemo<ColumnDef<VaultNoteSummary>[]>(
    () => [
      { accessorKey: "path", header: "Path" },
      {
        accessorKey: "size",
        header: "Size",
        size: 80,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? `${(v / 1024).toFixed(1)} KB` : "—";
        },
      },
      {
        accessorKey: "mtime",
        header: "Modified",
        size: 160,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? new Date(v * 1000).toLocaleString() : "—";
        },
      },
    ],
    [],
  );

  const handleTabChange = useCallback(
    (value: string) => {
      setActiveTab(value);
      if (value === "activity" && commits.length === 0) {
        loadCommits();
      }
    },
    [commits.length, loadCommits],
  );

  return (
    <div>
      <PageHeader
        eyebrow="Memory"
        title="Obsidian Vault"
        actions={<RefreshButton onRefresh={doFetch} />}
      />

      <div className={styles.statusRow}>
        <Stat
          eyebrow="Status"
          value="—"
          sub={
            status != null ? (
              <Pill variant={status.connected ? "success" : "error"}>
                {status.connected ? "Connected" : "Disconnected"}
              </Pill>
            ) : undefined
          }
          plain
        />
        <Stat eyebrow="Notes" value={status?.note_count ?? "—"} />
        <Stat
          eyebrow="Last Commit"
          value={status?.last_commit ? status.last_commit.sha.slice(0, 8) : "—"}
        />
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="notes">Notes</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="notes">
          <div className={styles.filterRow}>
            <Select value={folder} onValueChange={setFolder} placeholder="All folders">
              <SelectItem value="">All folders</SelectItem>
              {folders.map((f) => (
                <SelectItem key={f} value={f}>{f}</SelectItem>
              ))}
            </Select>
            <input
              type="text"
              className={styles.searchInput}
              placeholder="Search notes..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <DataTable
            data={filteredNotes}
            columns={noteColumns}
            getRowId={(r) => r.path}
            onRowClick={handleRowClick}
            loading={loading}
            pageSize={50}
            emptyState="No notes found"
          />
        </TabsContent>

        <TabsContent value="activity">
          <CommitHistory commits={commits} loading={commitsLoading} />
        </TabsContent>
      </Tabs>

      <NoteViewer
        note={selectedNote}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
