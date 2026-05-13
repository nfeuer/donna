import client from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface VaultNoteSummary {
  path: string;
  mtime: number | null;
  size: number | null;
}

export interface VaultNote {
  path: string;
  content: string;
  frontmatter: Record<string, unknown>;
  mtime: number;
  size: number;
}

export interface VaultStatus {
  connected: boolean;
  root?: string;
  note_count: number;
  last_commit: { sha: string; message: string } | null;
}

export interface VaultCommit {
  sha: string;
  message: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchNotes(
  params: { folder?: string } = {},
): Promise<{ notes: VaultNoteSummary[]; count: number }> {
  const query: Record<string, string> = {};
  if (params.folder) query.folder = params.folder;
  const { data } = await client.get("/admin/vault/notes", { params: query });
  return data;
}

export async function fetchNote(path: string): Promise<VaultNote> {
  const { data } = await client.get(`/admin/vault/notes/${path}`);
  return data;
}

export async function fetchVaultStatus(): Promise<VaultStatus> {
  const { data } = await client.get("/admin/vault/status");
  return data;
}

export async function fetchVaultHistory(
  params: { limit?: number } = {},
): Promise<{ commits: VaultCommit[]; count: number }> {
  const query: Record<string, number> = {};
  if (params.limit) query.limit = params.limit;
  const { data } = await client.get("/admin/vault/history", { params: query });
  return data;
}
