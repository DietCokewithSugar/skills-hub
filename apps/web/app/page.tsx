import { AppShell } from "@/components/shell/AppShell";
import { NewSession } from "@/components/shell/NewSession";
import { SessionList } from "@/components/shell/SessionList";
import { API_BASE } from "@/lib/api";
import type { SessionSummary, SkillSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/** RSC 直接拉，不经过客户端 —— 首屏不该等一次往返。 */
async function fetchJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;   // 后端没起时页面仍然可读，而不是白屏
  }
}

export default async function Home() {
  const [skills, sessions] = await Promise.all([
    fetchJson<SkillSummary[]>("/api/skills", []),
    fetchJson<{ items: SessionSummary[] }>("/api/sessions", { items: [] }),
  ]);

  return (
    <AppShell rail={<SessionList sessions={sessions.items} />}>
      <NewSession skills={skills} />
    </AppShell>
  );
}
