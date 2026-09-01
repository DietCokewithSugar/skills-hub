"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useMotionToken } from "@/lib/motion";
import { useEventStream } from "@/lib/sse";
import { hydrate, reduce, type SessionState, type StepView } from "@/lib/store";
import type {
  ArtifactInfo, BenchEvent, Card, CardRequestPayload, Part, StepInfo,
} from "@/lib/types";
import { CardRenderer } from "../cards/registry";
import { PartView } from "../parts/PartView";
import { StatusLamp, type LampState } from "../ui/StatusLamp";
import { LogTail } from "./LogTail";
import { ProgressBar } from "./ProgressBar";
import styles from "./RunSpine.module.css";
import { ThinkingStream } from "./ThinkingStream";
import { Timer } from "./Timer";

const LAMP: Record<string, LampState> = {
  pending: "pending", running: "running", succeeded: "succeeded",
  waiting: "waiting", failed: "failed",
};

export function RunSpine({
  sessionId, initialParts, initialMaxSeq, initialRunId,
  initialSteps, stepLabels, onArtifacts,
}: {
  sessionId: string;
  initialParts: Part[];
  initialMaxSeq: number;
  initialRunId: string | null;
  initialSteps: StepInfo[];
  stepLabels: Record<string, string>;
  onArtifacts?: () => void;
}) {
  const [state, dispatch] = useReducer(
    reduce,
    { parts: initialParts, maxSeq: initialMaxSeq, steps: initialSteps, labels: stepLabels },
    (a) => ({
      ...hydrate(a.parts, a.maxSeq, a.steps, a.labels),
      labels: a.labels,
      runId: initialRunId,
    }),
  );
  const [pendingCard, setPendingCard] = useState<Card | null>(null);
  const [cardBusy, setCardBusy] = useState(false);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});

  useEventStream(sessionId, initialMaxSeq, (ev: BenchEvent) => {
    dispatch(ev);
    if (ev.type === "artifact.created") onArtifacts?.();
  });

  const runId = state.runId;

  // 待作答卡片从后端取权威状态（刷新/隔天再来都拿得到）。
  // 没有待作答卡片时后端返回 204 → null，不是错误。
  useEffect(() => {
    let cancelled = false;
    if (state.phase !== "waiting" || !runId) { setPendingCard(null); return; }
    api.getPendingCard(runId)
      .then((c) => { if (!cancelled) setPendingCard(c ?? null); })
      .catch(() => { if (!cancelled) setPendingCard(null); });
    return () => { cancelled = true; };
  }, [state.phase, runId, state.maxSeq]);

  // 7.5：页面标题加 ● 前缀，浏览器标签页也能看出「在等你」
  useEffect(() => {
    const base = document.title.replace(/^●\s*/, "");
    document.title = state.phase === "waiting" ? `● ${base}` : base;
  }, [state.phase]);

  const submitCard = async (action: string, values: Record<string, unknown>) => {
    if (!pendingCard || !runId) return;
    setCardBusy(true);
    setCardErrors({});
    try {
      await api.resumeRun(runId, pendingCard.id, action, values);
      setPendingCard(null);
    } catch (e) {
      // 后端 422 的字段级错误优先 —— 前端校验可以被绕过
      if (e instanceof ApiError) setCardErrors(e.fieldErrors);
    } finally {
      setCardBusy(false);
    }
  };

  const retry = async (stepId: string | null) => {
    if (!runId) return;
    const run = await api.retryRun(runId, stepId ?? undefined);
    dispatch({ type: "run.started", seq: null, data: { run_id: run.id } });
  };

  const thinking = state.phase === "thinking";
  const spineAnimates = useMotionToken("spine", "thinking", thinking);
  const dimmed = state.phase === "waiting";

  // ── 按工序组织内容（7.4：每个 step 是挂在 spine 上的节点）──
  const { preamble, grouped } = useMemo(() => {
    const shown = state.parts.filter((p) => {
      if (p.type !== "card_request") return true;
      return (p.payload as unknown as CardRequestPayload).status !== "pending";
    });
    const pre: Part[] = [];
    const by: Record<string, Part[]> = {};
    for (const p of shown) {
      // Part 上盖了执行时的 step_id（见 events/emitter.py），
      // 所以刷新之后内容仍然挂在正确的工序下
      const sid = (p.payload as any).step_id as string | undefined;
      if (sid && state.steps[sid]) (by[sid] ??= []).push(p);
      else pre.push(p);
    }
    return { preamble: pre, grouped: by };
  }, [state.parts, state.steps]);

  return (
    <div className={styles.wrap}>
      {/* 7.8：SSE 状态同步进 aria-live 供读屏用户获知 */}
      <div className="sr-only" role="status" aria-live="polite">
        {state.announcement}
      </div>

      <div className={`${styles.spine} ${spineAnimates ? styles.spineThinking : ""}`}>
        <div className={dimmed ? styles.dimmed : styles.focused}>
          {preamble.map((p) => (
            <PartView key={`${p.seq}-${p.id}`} part={p}
                      role={p.type === "text" && p.seq === 1 ? "user" : undefined}
                      onRetry={retry} />
          ))}

          {state.stepOrder.map((id) => (
            <StepNode
              key={id}
              step={state.steps[id]}
              isCurrent={state.currentStep === id}
              parts={grouped[id] ?? []}
              onRetry={retry}
            />
          ))}

          {/* 流式推理（尚未落 Part） */}
          {state.streamingReasoning ? (
            <ThinkingStream text={state.streamingReasoning} live />
          ) : null}
        </div>

        {/* 「在等我」：卡片保持 100% 不透明度 */}
        {pendingCard ? (
          <div className={styles.focused}>
            <CardRenderer
              spec={pendingCard.spec}
              expiresAt={pendingCard.expires_at}
              busy={cardBusy}
              serverErrors={cardErrors}
              onSubmit={submitCard}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function StepNode({
  step, isCurrent, parts, onRetry,
}: {
  step: StepView;
  isCurrent: boolean;
  parts: Part[];
  onRetry: (stepId: string | null) => void;
}) {
  const running = step.status === "running";
  // 半环旋转 —— 全页唯一的持续运动，由令牌决定谁能转
  const animate = useMotionToken(`step:${step.stepId}`, "acting", running && isCurrent);

  return (
    <div className={styles.node}>
      <div className={styles.marker}>
        <StatusLamp state={LAMP[step.status] ?? "pending"} animate={animate} />
      </div>

      <div className={styles.head}>
        <span className={styles.label}>{step.label}</span>
        <span className={styles.meta}>
          <span className={styles.timer}>
            <Timer startedAt={step.startedAt} endedAt={step.endedAt} />
          </span>
        </span>
      </div>

      {running && step.progressMsg ? (
        <div className={styles.sub}>
          {step.progressMsg}
          {step.pct != null ? <span className="num"> {Math.round(step.pct)}%</span> : null}
        </div>
      ) : null}
      {running ? <ProgressBar pct={step.pct} label={step.label} /> : null}
      {running ? <LogTail lines={step.logTail} /> : null}

      {parts.length ? (
        <div className={styles.nodeBody}>
          {parts.map((p) => (
            <PartView key={`${p.seq}-${p.id}`} part={p} onRetry={onRetry} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
