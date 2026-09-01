"use client";

import { useMemo, useState } from "react";
import styles from "./ThinkingStream.module.css";

const COLLAPSE_AFTER = 6;   // 超过 6 行自动折叠
const KEEP_VISIBLE = 2;     // 折叠后保留最近 2 行滚动

/**
 * 推理流（7.5「在想」）。
 *
 * 数据源是 DeepSeek 思考模式返回的推理内容 —— 展示的是真实推理文本。
 * 超过 6 行自动折叠为「已推理 N 行 · 展开」，保留最近 2 行。
 */
export function ThinkingStream({ text, live }: { text: string; live?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const lines = useMemo(
    () => text.split("\n").map((l) => l.trim()).filter(Boolean),
    [text],
  );

  if (lines.length === 0) return null;

  const shouldCollapse = lines.length > COLLAPSE_AFTER && !expanded;
  const shown = shouldCollapse ? lines.slice(-KEEP_VISIBLE) : lines;

  return (
    <div className={styles.stream}>
      {shouldCollapse ? (
        <button className={styles.toggle} onClick={() => setExpanded(true)}>
          已推理 {lines.length} 行 · 展开
        </button>
      ) : null}

      {shown.map((line, i) => (
        // key 用行内容 + 位置：内容追加时已渲染的行不会重新触发淡入
        <div key={`${shown.length}-${i}-${line.slice(0, 12)}`} className={styles.line}>
          {line}
        </div>
      ))}

      {expanded && lines.length > COLLAPSE_AFTER ? (
        <button className={styles.toggle} onClick={() => setExpanded(false)}>
          收起
        </button>
      ) : null}

      {live ? <span className="sr-only">正在推理</span> : null}
    </div>
  );
}
