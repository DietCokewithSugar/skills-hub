"use client";

import { useCallback, useState } from "react";
import { RunSpine } from "@/components/spine/RunSpine";
import * as api from "@/lib/api";
import type {
  ArtifactInfo, Part, SessionSummary, SkillSummary, StepInfo,
} from "@/lib/types";
import { AppShell } from "./AppShell";
import { SessionStarter } from "./SessionStarter";
import { WorkspacePanel } from "./WorkspacePanel";

/**
 * 会话视图，拥有整个三栏布局。
 *
 * 由它持有产物状态 —— 产物是执行中陆续产生的，右栏必须跟着 SSE 刷新，
 * 不能停在服务端渲染那一刻的快照。
 *
 * 历史 Part 与持久化的步骤状态由 RSC 拉好传进来（R2 回放），实时事件由
 * RunSpine 接 SSE。两者走同一个 reducer，所以「回放内容与实时看到的
 * 完全一致」。
 */
export function SessionView({
  rail, session, skill, parts, maxSeq, steps, artifacts,
}: {
  rail: React.ReactNode;
  session: SessionSummary;
  skill: SkillSummary | null;
  parts: Part[];
  maxSeq: number;
  steps: StepInfo[];
  artifacts: ArtifactInfo[];
}) {
  const [runId, setRunId] = useState<string | null>(session.latest_run_id);
  const [files, setFiles] = useState<ArtifactInfo[]>(artifacts);
  const started = parts.length > 0 || runId !== null;

  const refreshArtifacts = useCallback(() => {
    api.listArtifacts(session.id).then(setFiles).catch(() => {});
  }, [session.id]);

  const labels = Object.fromEntries(
    (skill?.steps ?? []).map((s) => [s.id, s.label || s.id]),
  );

  return (
    <AppShell rail={rail} workspace={<WorkspacePanel artifacts={files} />}>
      {!started ? (
        <SessionStarter sessionId={session.id} skill={skill} onStarted={setRunId} />
      ) : null}
      <RunSpine
        sessionId={session.id}
        initialParts={parts}
        initialMaxSeq={maxSeq}
        initialRunId={runId}
        initialSteps={steps}
        stepLabels={labels}
        onArtifacts={refreshArtifacts}
      />
    </AppShell>
  );
}
