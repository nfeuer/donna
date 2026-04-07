import client from "./client";

export interface AdminHealthData {
  status: "healthy" | "degraded";
  checks: Record<string, { ok: boolean; detail?: string }>;
  uptime_seconds: number;
  timestamp: string;
}

export async function fetchAdminHealth(): Promise<AdminHealthData> {
  const { data } = await client.get("/admin/health");
  return data;
}
