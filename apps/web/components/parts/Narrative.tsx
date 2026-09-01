"use client";

import styles from "./Parts.module.css";

/**
 * 叙述段落。
 *
 * llm step 的输出是通过 schema 校验的结构化 JSON。**不要把 JSON 原样倒进
 * 消息流** —— 这是工作台，不是调试控制台。这里按 summary / findings /
 * recommendations 排成给人读的形态。
 *
 * 里面的每个数字都已经过数字越界拦截（bench/accuracy/number_guard.py），
 * 所以可以放心直接展示。
 */
export function Narrative({ payload }: { payload: Record<string, unknown> }) {
  const summary = typeof payload.summary === "string" ? payload.summary : "";
  const findings = Array.isArray(payload.findings) ? payload.findings : [];
  const recs = Array.isArray(payload.recommendations) ? payload.recommendations : [];

  if (!summary && findings.length === 0 && recs.length === 0) return null;

  return (
    <div className={styles.narrative}>
      {summary ? <p className={styles.text}>{summary}</p> : null}
      {findings.length ? (
        <>
          <h3 className={styles.narrHead}>主要发现</h3>
          <ol className={styles.narrList}>
            {findings.map((f, i) => <li key={i}>{String(f)}</li>)}
          </ol>
        </>
      ) : null}
      {recs.length ? (
        <>
          <h3 className={styles.narrHead}>改进建议</h3>
          <ol className={styles.narrList}>
            {recs.map((r, i) => <li key={i}>{String(r)}</li>)}
          </ol>
        </>
      ) : null}
    </div>
  );
}

/** 判断一段 text Part 是否其实是结构化叙述（llm step 的输出）。 */
export function parseNarrative(text: string): Record<string, unknown> | null {
  const t = text.trim();
  if (!t.startsWith("{")) return null;
  try {
    const obj = JSON.parse(t);
    if (obj && typeof obj === "object" &&
        ("summary" in obj || "findings" in obj || "recommendations" in obj)) {
      return obj as Record<string, unknown>;
    }
  } catch {
    /* 不是 JSON 就当普通文本 */
  }
  return null;
}
