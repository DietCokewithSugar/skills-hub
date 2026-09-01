/**
 * 会话状态：按 seq 归并的 Part 列表 + 由事件推导的执行状态。
 *
 * R2 验收：「回放内容与实时看到的完全一致」。做法是**只有一条路径**——
 * 历史 Part 和实时 part.created 事件都走 `mergePart`，按 seq 插入并去重。
 * 没有「实时分支」和「回放分支」两套渲染逻辑，所以两者不可能不一致。
 */

import type { BenchEvent, ErrorPayload, Part, RunStatus, StepInfo } from "./types";

/** 7.5 的三种状态：在想 / 在做 / 在等我 */
export type Phase = "idle" | "thinking" | "acting" | "waiting" | "done" | "failed";

export interface StepView {
  stepId: string;
  label: string;
  status: "pending" | "running" | "succeeded" | "failed" | "waiting";
  /** 真实开始时间戳（毫秒），计时器据此算真实经过时间 */
  startedAt: number | null;
  endedAt: number | null;
  /** 只有拿到 stdout JSONL 的真实进度才有值；没有就不画假进度（7.5） */
  pct: number | null;
  progressMsg: string;
  logTail: string[];
}

export interface SessionState {
  parts: Part[];
  /** step_id -> 人类可读标签，来自 skill manifest */
  labels: Record<string, string>;
  maxSeq: number;
  phase: Phase;
  runId: string | null;
  runStatus: RunStatus | null;
  currentStep: string | null;
  steps: Record<string, StepView>;
  stepOrder: string[];
  /** 流式推理的暂存（未落 Part 前） */
  streamingReasoning: string;
  error: ErrorPayload | null;
  tokenIn: number;
  tokenOut: number;
  /** 供 aria-live 播报的一句话（7.8） */
  announcement: string;
}

/** 冻结：防止有人再写出 `{...initialState}` 然后就地 push 的共享可变状态。 */
export const initialState: SessionState = Object.freeze({
  parts: [], labels: {}, maxSeq: 0, phase: "idle", runId: null, runStatus: null,
  currentStep: null, steps: {}, stepOrder: [], streamingReasoning: "",
  error: null, tokenIn: 0, tokenOut: 0, announcement: "",
}) as SessionState;

const MAX_LOG_TAIL = 200;

/** 按 seq 插入，重复的丢弃。SSE 已经保证不重不漏，这里是最后一道保险。 */
export function mergePart(parts: Part[], incoming: Part): Part[] {
  if (parts.length === 0) return [incoming];
  const last = parts[parts.length - 1];
  if (incoming.seq > last.seq) return [...parts, incoming];

  const idx = parts.findIndex((p) => p.seq === incoming.seq);
  if (idx >= 0) {
    // 同 seq 再次到达 = 流式片段定稿（part.completed），就地替换
    const next = parts.slice();
    next[idx] = incoming;
    return next;
  }
  const at = parts.findIndex((p) => p.seq > incoming.seq);
  const next = parts.slice();
  next.splice(at < 0 ? parts.length : at, 0, incoming);
  return next;
}

function upsertStep(s: SessionState, stepId: string,
                    patch: Partial<StepView>): SessionState {
  const existing = s.steps[stepId] ?? {
    stepId, label: s.labels[stepId] ?? stepId, status: "pending" as const,
    startedAt: null, endedAt: null, pct: null, progressMsg: "", logTail: [],
  };
  return {
    ...s,
    steps: { ...s.steps, [stepId]: { ...existing, ...patch } },
    stepOrder: s.stepOrder.includes(stepId) ? s.stepOrder : [...s.stepOrder, stepId],
  };
}

export function reduce(state: SessionState, ev: BenchEvent): SessionState {
  const d = ev.data ?? {};

  switch (ev.type) {
    case "heartbeat":
      return state;

    // ── Part 流 ────────────────────────────────────────────
    case "part.created":
    case "part.completed": {
      if (ev.seq == null) return state;
      const part: Part = {
        id: String(d.part_id ?? `seq-${ev.seq}`),
        seq: ev.seq,
        type: d.type,
        payload: d.payload ?? {},
        created_at: new Date().toISOString(),
      };
      let next: SessionState = {
        ...state,
        parts: mergePart(state.parts, part),
        maxSeq: Math.max(state.maxSeq, ev.seq),
      };
      // 推理落成 Part 后，流式暂存就该清掉，否则会显示两遍
      if (part.type === "reasoning") next.streamingReasoning = "";
      if (part.type === "error") {
        next.error = part.payload as unknown as ErrorPayload;
        next.phase = "failed";
        next.announcement = `执行失败：${(part.payload as any).message ?? ""}`;
      }
      if (part.type === "card_request" && (part.payload as any).status === "pending") {
        next.phase = "waiting";
        next.announcement = `需要你确认：${(part.payload as any).spec?.title ?? ""}`;
      }
      if (part.type === "artifact") {
        next.announcement = `已生成产物 ${(part.payload as any).filename}`;
      }
      return next;
    }

    case "part.delta": {
      // 推理内容流式到达 —— R7「在想」的数据源
      if (d.type === "reasoning") {
        return {
          ...state,
          phase: state.phase === "waiting" ? state.phase : "thinking",
          streamingReasoning: state.streamingReasoning + String(d.text ?? ""),
        };
      }
      return state;
    }

    // ── Run 生命周期 ───────────────────────────────────────
    case "run.started":
      return {
        ...state, runId: d.run_id ?? state.runId, runStatus: "running",
        phase: "acting", error: null,
        announcement: "已受理，开始执行",
      };

    case "run.resumed":
      return { ...state, runStatus: "running", phase: "acting",
               announcement: "已提交，继续执行" };

    case "run.waiting":
      return { ...state, runStatus: "waiting_for_input", phase: "waiting" };

    case "run.completed":
      return { ...state, runStatus: "succeeded", phase: "done",
               currentStep: null, announcement: "执行完成" };

    case "run.failed":
      return {
        ...state, runStatus: "failed", phase: "failed",
        error: (d as unknown as ErrorPayload) ?? state.error,
        announcement: `执行失败：${d.message ?? ""}`,
      };

    case "run.cancelled":
      return { ...state, runStatus: "cancelled", phase: "idle",
               announcement: "已取消" };

    case "run.expired":
      return { ...state, runStatus: "expired", phase: "failed",
               announcement: "这一步等待超时，已挂起" };

    // ── Step ──────────────────────────────────────────────
    case "step.started": {
      const s = upsertStep(state, d.step_id, {
        label: d.label || state.labels[d.step_id] || d.step_id,
        status: "running",
        startedAt: Date.now(),
        endedAt: null,
        pct: null,
      });
      return { ...s, currentStep: d.step_id, phase: "acting",
               announcement: `正在${d.label ?? d.step_id}` };
    }

    case "step.progress":
      return upsertStep(state, d.step_id, {
        pct: typeof d.pct === "number" ? d.pct : null,
        progressMsg: d.msg ?? "",
      });

    case "step.log": {
      const cur = state.steps[d.step_id]?.logTail ?? [];
      const tail = [...cur, String(d.line ?? "")].slice(-MAX_LOG_TAIL);
      return upsertStep(state, d.step_id, { logTail: tail });
    }

    case "step.completed":
      return upsertStep(state, d.step_id, {
        status: "succeeded", endedAt: Date.now(), pct: null,
      });

    case "step.failed":
      return upsertStep(state, d.step_id, {
        status: "failed", endedAt: Date.now(),
      });

    // ── 卡片 ──────────────────────────────────────────────
    case "card.requested":
      return { ...upsertStep(state, d.step_id, { status: "waiting" }),
               phase: "waiting" };

    case "card.expired":
      return { ...state, phase: "failed",
               announcement: "这一步等待超过时限，已挂起" };

    case "usage.updated":
      return {
        ...state,
        tokenIn: state.tokenIn + (d.token_in ?? 0),
        tokenOut: state.tokenOut + (d.token_out ?? 0),
      };

    default:
      return state;
  }
}

/**
 * 用历史 Part + 持久化的步骤状态初始化。
 *
 * step.started/completed 是**瞬时事件，不落库** —— 光靠它们，刷新页面后
 * 工序时间轴就没了，违反 R2「回放内容与实时看到的完全一致」。
 * 所以步骤状态从 GET /runs/{id} 读回来（step_runs 表），带真实起止时间。
 */
export function hydrate(
  parts: Part[], maxSeq: number, steps: StepInfo[] = [],
  labels: Record<string, string> = {},
): SessionState {
  // 注意不要写成 { ...initialState, ... }：那样 steps/stepOrder 拿到的是
  // 模块级常量里**同一个**对象与数组的引用，下面的写入会污染它，
  // 于是第二次 hydrate 就带上了上一次的步骤（React 报重复 key）。
  const s: SessionState = {
    ...initialState, parts, maxSeq, labels,
    steps: {}, stepOrder: [],
  };

  for (const st of steps) {
    const startedAt = st.started_at ? new Date(st.started_at).getTime() : null;
    const endedAt = st.ended_at ? new Date(st.ended_at).getTime() : null;
    s.steps[st.step_id] = {
      stepId: st.step_id,
      label: labels[st.step_id] ?? st.step_id,
      status:
        st.status === "succeeded" ? "succeeded" :
        st.status === "failed" ? "failed" :
        st.status === "running" ? "running" :
        st.status === "waiting_for_input" ? "waiting" : "pending",
      startedAt, endedAt, pct: null, progressMsg: "", logTail: [],
    };
    s.stepOrder.push(st.step_id);
  }

  const lastErr = [...parts].reverse().find((p) => p.type === "error");
  const pendingCard = [...parts].reverse().find(
    (p) => p.type === "card_request" && (p.payload as any).status === "pending",
  );
  if (pendingCard) s.phase = "waiting";
  else if (lastErr) {
    s.phase = "failed";
    s.error = lastErr.payload as unknown as ErrorPayload;
  } else if (parts.length) s.phase = "idle";
  return s;
}
