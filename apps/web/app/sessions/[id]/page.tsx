import { notFound } from "next/navigation";
import { SessionList } from "@/components/shell/SessionList";
import { SessionView } from "@/components/shell/SessionView";
import { API_BASE } from "@/lib/api";
import type {
  ArtifactInfo, Part, RunDetail, SessionSummary, SkillSummary,
} from "@/lib/types";

export const dynamic = "force-dynamic";

async function fetchJson<T>(path: string, fallback: T): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (res.status === 404) return null;
    if (!res.ok) return fallback;
    if (res.status === 204) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;   // 后端没起时页面仍然可读，而不是白屏
  }
}

export default async function SessionPage({
  params,
}: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const session = await fetchJson<SessionSummary>(`/api/sessions/${id}`, null as never);
  if (!session) notFound();

  // R2：历史 Part + 持久化的步骤状态都在服务端拉好，
  // 客户端只负责从 maxSeq 之后接实时事件
  const [partsRes, sessionsRes, skills, artifacts, run] = await Promise.all([
    fetchJson<{ items: Part[]; max_seq: number }>(
      `/api/sessions/${id}/parts`, { items: [], max_seq: 0 }),
    fetchJson<{ items: SessionSummary[] }>("/api/sessions", { items: [] }),
    fetchJson<SkillSummary[]>("/api/skills", []),
    fetchJson<ArtifactInfo[]>(`/api/sessions/${id}/artifacts`, []),
    session.latest_run_id
      ? fetchJson<RunDetail>(`/api/runs/${session.latest_run_id}`, null as never)
      : Promise.resolve(null),
  ]);

  const skill = (skills ?? []).find((s) => s.id === session.skill_id) ?? null;

  return (
    <SessionView
      rail={<SessionList sessions={sessionsRes?.items ?? []} activeId={id} />}
      session={session}
      skill={skill}
      parts={partsRes?.items ?? []}
      maxSeq={partsRes?.max_seq ?? 0}
      steps={run?.steps ?? []}
      artifacts={artifacts ?? []}
    />
  );
}
