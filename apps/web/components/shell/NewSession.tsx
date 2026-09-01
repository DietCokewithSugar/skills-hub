"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import * as api from "@/lib/api";
import type { SkillSummary } from "@/lib/types";
import styles from "./NewSession.module.css";

const STEP_LABEL: Record<string, string> = {
  python: "计算", interaction: "确认", llm: "撰写",
};

export function NewSession({ skills }: { skills: SkillSummary[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const start = async (skill: SkillSummary) => {
    setBusy(skill.id);
    setError("");
    try {
      const s = await api.createSession(skill.id, skill.name);
      router.push(`/sessions/${s.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建会话失败");
      setBusy(null);
    }
  };

  return (
    <div className={styles.wrap}>
      <h1 className={styles.lede}>选一个 skill 开始</h1>
      <p className={styles.sub}>
        选定后上传数据，中途会有确认卡片，最后拿到 Word 报告。
      </p>

      {skills.length === 0 ? (
        <p className={styles.error}>
          没有已注册的 skill。检查 BENCH_SKILLS_DIR 指向的目录，
          或访问 /api/skills/errors 看加载失败的原因。
        </p>
      ) : (
        <div className={styles.skills}>
          {skills.map((s) => (
            <button key={s.id} className={styles.skill}
                    onClick={() => start(s)} disabled={busy !== null}>
              <span className={styles.name}>{s.name}</span>
              <span className={`${styles.version} num`}>{s.version}</span>
              <div className={styles.desc}>{s.description}</div>
              <div className={styles.steps}>
                {s.steps.map((st) => STEP_LABEL[st.type] ?? st.type).join(" → ")}
                {s.outputs.length ? ` → ${s.outputs[0].label}` : ""}
              </div>
            </button>
          ))}
        </div>
      )}

      {error ? <p className={styles.error}>{error}</p> : null}
    </div>
  );
}
