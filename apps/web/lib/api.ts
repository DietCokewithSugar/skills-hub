/** 后端 17 个端点的类型化封装。 */

import type {
  ArtifactInfo, Card, FieldErrors, Part, Run, RunDetail,
  SessionSummary, SkillSummary,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** R5：卡片校验失败时后端给的字段级错误 */
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    let fields: Record<string, string> = {};
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        const fe = detail as FieldErrors;
        message = fe.message ?? message;
        fields = fe.field_errors ?? {};
      }
    } catch {
      /* 响应不是 JSON，保留 HTTP 状态码作为消息 */
    }
    throw new ApiError(res.status, message, fields);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── skills ──────────────────────────────────────────────────
export const listSkills = () => request<SkillSummary[]>("/api/skills");
export const skillErrors = () => request<Record<string, string>>("/api/skills/errors");

// ── sessions ────────────────────────────────────────────────
export const listSessions = () =>
  request<{ items: SessionSummary[]; next_cursor: string | null }>("/api/sessions");

export const getSession = (id: string) =>
  request<SessionSummary>(`/api/sessions/${id}`);

export const createSession = (skill_id: string, title?: string) =>
  request<SessionSummary>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ skill_id, title }),
  });

export const deleteSession = (id: string) =>
  request<void>(`/api/sessions/${id}`, { method: "DELETE" });

/** R2 回放。afterSeq 与 SSE 的 Last-Event-ID 是同一个坐标系。 */
export const getParts = (id: string, afterSeq = 0) =>
  request<{ items: Part[]; max_seq: number }>(
    `/api/sessions/${id}/parts?after_seq=${afterSeq}`,
  );

export const listArtifacts = (id: string) =>
  request<ArtifactInfo[]>(`/api/sessions/${id}/artifacts`);

export async function uploadInput(sessionId: string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return request<{ filename: string; size_bytes: number; sha256: string }>(
    `/api/sessions/${sessionId}/upload`,
    { method: "POST", body: fd },
  );
}

/** 返回 202 + run_id —— R7：提交后立即有「已受理」反馈 */
export const postMessage = (
  sessionId: string,
  text: string,
  inputs: Record<string, unknown> = {},
) =>
  request<Run>(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ text, inputs }),
  });

// ── runs ────────────────────────────────────────────────────
export const getRun = (id: string) => request<RunDetail>(`/api/runs/${id}`);
/** 没有待作答卡片时后端返回 204，这里表现为 null（不是错误）。 */
export const getPendingCard = (runId: string) =>
  request<Card | null>(`/api/runs/${runId}/card`);

export const resumeRun = (
  runId: string,
  cardId: string,
  action: string,
  values: Record<string, unknown>,
) =>
  request<Run>(`/api/runs/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ card_id: cardId, action, values }),
  });

export const cancelRun = (runId: string) =>
  request<Run>(`/api/runs/${runId}/cancel`, { method: "POST" });

/** R8：从失败步骤重试，追加新 Run 而不是整个重跑 */
export const retryRun = (runId: string, fromStep?: string) =>
  request<Run>(
    `/api/runs/${runId}/retry${fromStep ? `?from_step=${encodeURIComponent(fromStep)}` : ""}`,
    { method: "POST" },
  );

// ── artifacts ───────────────────────────────────────────────
/** 302 到签名 URL，前端不碰存储直链（R6） */
export const artifactDownloadUrl = (id: string, preview = false) =>
  `${API_BASE}/api/artifacts/${id}/download${preview ? "?preview=true" : ""}`;

export const sseUrl = (sessionId: string, afterSeq: number) =>
  `${API_BASE}/api/sessions/${sessionId}/events?after_seq=${afterSeq}`;
