/**
 * 与后端 bench/api/schemas.py 对应的类型。
 *
 * P2-R17：API 用 OpenAPI 描述并生成 SDK。在装上代码生成之前，
 * 这里手写对齐 —— 后端改了字段，这里 tsc 会报错，不会静默漂移。
 */

// ── Part（PRD 3.2 概念模型）─────────────────────────────────
export type PartType =
  | "text" | "reasoning" | "tool_call" | "tool_result"
  | "card_request" | "card_response" | "artifact" | "error";

export type Track = "trusted" | "exploratory";

export interface Part {
  id: string;
  seq: number;
  type: PartType;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TextPayload { text: string }
export interface ReasoningPayload { text: string }

export interface ToolCallPayload {
  name: string;
  args: Record<string, unknown>;
  /** R9：模型生成的代码全文，执行前对用户完整可见 */
  code: string | null;
  track: Track;
}

export interface ToolResultPayload {
  name: string;
  ok: boolean;
  result: unknown;
  track: Track;
  stdout: string;
  stderr: string;
}

export interface ArtifactPayload {
  artifact_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  track: Track;
  has_preview: boolean;
}

/** 7.7：失败要说清哪一步、什么原因、下一步怎么办 */
export interface ErrorPayload {
  step_id: string | null;
  message: string;
  detail: Record<string, unknown>;
  retryable: boolean;
}

export interface CardRequestPayload {
  step_id: string;
  spec: CardSpec;
  status: "pending" | "answered";
}

export interface CardResponsePayload {
  card_id: string;
  step_id: string;
  action: string;
  values: Record<string, unknown>;
  /** 后端给的一行摘要，前端不自己拼（7.5 状态转换）*/
  summary: string;
  cancelled: boolean;
}

// ── 卡片（R5，v1 收敛为 6 种）───────────────────────────────
export type CardType =
  | "confirm" | "select" | "multi_select"
  | "form" | "file_pick" | "table_review";

export interface CardAction {
  id: string;
  label: string;
  primary: boolean;
  cancels: boolean;
}

export interface FormField {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "date" | "textarea" | "checkbox";
  required: boolean;
  default: unknown;
  options: string[];
  min: number | null;
  max: number | null;
  placeholder: string;
  help: string;
}

interface CardBase {
  title: string;
  body: string;
  actions: CardAction[];
  timeout_hours: number;
}

export type CardSpec =
  | (CardBase & { type: "confirm" })
  | (CardBase & { type: "select"; key: string; options: string[]; default: string | null })
  | (CardBase & { type: "multi_select"; key: string; options: string[];
                  default: string[]; min_selected: number; max_selected: number | null })
  | (CardBase & { type: "form"; fields: FormField[] })
  | (CardBase & { type: "file_pick"; key: string; candidates: string[];
                  accept: string[]; multiple: boolean })
  | (CardBase & { type: "table_review"; key: string; columns: string[];
                  rows: Record<string, unknown>[]; default_accepted: boolean;
                  row_id_key: string });

export interface Card {
  id: string;
  run_id: string;
  step_id: string;
  spec: CardSpec;
  status: "pending" | "answered" | "expired" | "cancelled";
  response: Record<string, unknown> | null;
  expires_at: string;
}

// ── Run / Session / Skill ───────────────────────────────────
export type RunStatus =
  | "queued" | "running" | "waiting_for_input"
  | "succeeded" | "failed" | "cancelled" | "expired";

export type SessionStatus =
  | "idle" | "running" | "waiting_for_input" | "failed" | "expired";

export type StepStatus =
  | "pending" | "running" | "waiting_for_input"
  | "succeeded" | "failed" | "skipped" | "cancelled";

export interface StepInfo {
  step_id: string;
  idx: number;
  type: "python" | "interaction" | "llm";
  track: Track;
  status: StepStatus;
  attempt: number;
  started_at: string | null;
  ended_at: string | null;
  error: ErrorPayload | null;
}

export interface Run {
  id: string;
  session_id: string;
  skill_id: string | null;
  skill_version: string | null;
  status: RunStatus;
  current_step: string | null;
  error: ErrorPayload | null;
  token_in: number;
  token_out: number;
  started_at: string | null;
  ended_at: string | null;
}

export interface RunDetail extends Run {
  steps: StepInfo[];
  pending_card_id: string | null;
}

export interface SessionSummary {
  id: string;
  skill_id: string | null;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  latest_run_id: string | null;
}

export interface SkillSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  inputs: { key: string; label: string; type: string; accept: string[]; required: boolean }[];
  steps: { id: string; type: string; label: string }[];
  outputs: { path: string; label: string }[];
}

export interface ArtifactInfo {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  track: Track;
  has_preview: boolean;
  created_at: string;
}

// ── SSE 事件（PRD 6.3）──────────────────────────────────────
export type EventType =
  | "run.started" | "run.completed" | "run.failed" | "run.cancelled"
  | "run.waiting" | "run.resumed" | "run.expired"
  | "part.created" | "part.delta" | "part.completed"
  | "step.started" | "step.progress" | "step.completed" | "step.failed" | "step.log"
  | "card.requested" | "card.answered" | "card.expired"
  | "artifact.created" | "usage.updated" | "heartbeat";

export interface BenchEvent {
  type: EventType;
  seq: number | null;
  data: Record<string, any>;
}

/** 字段级错误：后端 422 的形状（R5 验收） */
export interface FieldErrors {
  message: string;
  field_errors: Record<string, string>;
}
