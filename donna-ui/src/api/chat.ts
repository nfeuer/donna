import client from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ChatIntent =
  | "task_query"
  | "task_action"
  | "agent_output_query"
  | "planning"
  | "freeform"
  | "escalation_request";

export type ChatSessionStatus = "active" | "expired" | "closed";
export type MessageRole = "user" | "assistant";

export interface ChatSession {
  id: string;
  user_id: string;
  channel: string;
  status: ChatSessionStatus;
  pinned_task_id?: string;
  summary?: string;
  created_at: string;
  last_activity: string;
  message_count: number;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  intent?: ChatIntent;
  tokens_used?: number;
  created_at: string;
}

export interface ChatResponse {
  text: string;
  session_id: string | null;
  needs_escalation: boolean;
  escalation_reason?: string;
  estimated_cost?: number;
  suggested_actions: string[];
  pin_suggestion?: Record<string, string>;
  session_pinned_task_id?: string;
}

export interface ContextStatus {
  used_tokens: number;
  max_tokens: number;
  compact_threshold: number;
  model_alias: string;
}

export interface SessionWithMessages {
  session: ChatSession;
  messages: ChatMessage[];
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function listSessions(
  params: { status?: string; channel?: string; limit?: number } = {},
): Promise<{ sessions: ChatSession[] }> {
  const { data } = await client.get("/chat/sessions", { params });
  return data;
}

export async function sendMessage(
  sessionId: string,
  text: string,
  channel = "api",
): Promise<ChatResponse> {
  const { data } = await client.post(`/chat/sessions/${sessionId}/messages`, {
    text,
    channel,
  });
  return data;
}

export async function fetchSession(
  sessionId: string,
): Promise<SessionWithMessages> {
  const { data } = await client.get(`/chat/sessions/${sessionId}`);
  return data;
}

export async function fetchMessages(
  sessionId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<{ messages: ChatMessage[] }> {
  const { data } = await client.get(`/chat/sessions/${sessionId}/messages`, {
    params,
  });
  return data;
}

export async function fetchContextStatus(
  sessionId: string,
): Promise<ContextStatus> {
  const { data } = await client.get(
    `/chat/sessions/${sessionId}/context-status`,
  );
  return data;
}

export async function pinSession(
  sessionId: string,
  taskId: string,
): Promise<{ status: string; task_id: string }> {
  const { data } = await client.post(`/chat/sessions/${sessionId}/pin`, {
    task_id: taskId,
  });
  return data;
}

export async function unpinSession(
  sessionId: string,
): Promise<{ status: string }> {
  const { data } = await client.delete(`/chat/sessions/${sessionId}/pin`);
  return data;
}

export async function escalateSession(
  sessionId: string,
): Promise<ChatResponse> {
  const { data } = await client.post(
    `/chat/sessions/${sessionId}/escalate`,
  );
  return data;
}

export async function closeSession(
  sessionId: string,
): Promise<{ status: string; summary?: string }> {
  const { data } = await client.delete(`/chat/sessions/${sessionId}`);
  return data;
}
