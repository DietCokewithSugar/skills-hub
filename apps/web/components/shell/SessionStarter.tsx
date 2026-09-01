"use client";

import { useState } from "react";
import * as api from "@/lib/api";
import type { SkillSummary } from "@/lib/types";
import styles from "./SessionStarter.module.css";

/**
 * 会话首屏：传输入、点开始。
 *
 * R7 验收：「提交后立即有明确的『已受理』反馈，不出现无响应的空白期」——
 * postMessage 返回 202 就立刻把控制权交给 RunSpine，不等执行结果。
 */
export function SessionStarter({
  sessionId, skill, onStarted,
}: {
  sessionId: string;
  skill: SkillSummary | null;
  onStarted: (runId: string) => void;
}) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!skill) {
    return <div className={styles.wrap}><p className={styles.sub}>该会话未绑定 skill。</p></div>;
  }

  const fileInputs = skill.inputs.filter((i) => i.type === "file");
  const missing = fileInputs.filter((i) => i.required && !files[i.key]);

  const upload = async (key: string, file: File) => {
    setError("");
    try {
      const r = await api.uploadInput(sessionId, file);
      setFiles((f) => ({ ...f, [key]: r.filename }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    }
  };

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const run = await api.postMessage(sessionId, `开始执行 ${skill.name}`, files);
      onStarted(run.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动失败");
      setBusy(false);
    }
  };

  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>{skill.name}</h1>
      <p className={styles.sub}>{skill.description}</p>

      <div className={styles.form}>
        {fileInputs.map((i) => (
          <div key={i.key}>
            <div className={styles.label}>
              {i.label}{i.required ? "" : "（可选）"}
            </div>
            <label className={styles.file}>
              <input
                type="file"
                accept={i.accept.join(",")}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload(i.key, f);
                }}
              />
              {files[i.key] ? (
                <div className={styles.picked}>已上传 {files[i.key]}</div>
              ) : (
                <div className={styles.hint}>接受 {i.accept.join("、") || "任意格式"}</div>
              )}
            </label>
          </div>
        ))}

        <div className={styles.row}>
          <button className={styles.go} onClick={start}
                  disabled={busy || missing.length > 0}>
            {busy ? "已受理…" : "开始执行"}
          </button>
          {missing.length > 0 ? (
            <span className={styles.hint}>
              还需要：{missing.map((m) => m.label).join("、")}
            </span>
          ) : null}
        </div>

        {error ? <p className={styles.error}>{error}</p> : null}
      </div>
    </div>
  );
}
