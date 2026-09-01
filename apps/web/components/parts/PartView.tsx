"use client";

import { useState } from "react";
import { artifactDownloadUrl } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type {
  ArtifactPayload, CardResponsePayload, ErrorPayload, Part,
  ToolCallPayload, ToolResultPayload,
} from "@/lib/types";
import { ThinkingStream } from "../spine/ThinkingStream";
import { Narrative, parseNarrative } from "./Narrative";
import styles from "./Parts.module.css";

/**
 * Part 渲染分发。
 *
 * PRD 3.2：「产物是 Part，不是执行的副作用。任何时候回看历史都能重新下载。」
 * 所以下载入口长在这里，而不是某个「本次执行结果」面板里。
 */
export function PartView({
  part, role, onRetry,
}: {
  part: Part;
  role?: "user" | "assistant";
  onRetry?: (stepId: string | null) => void;
}) {
  switch (part.type) {
    case "text": {
      const text = String((part.payload as any).text ?? "");
      if (!text) return null;
      // llm step 的结构化输出排成给人读的形态，而不是把 JSON 倒出来
      const narrative = parseNarrative(text);
      if (narrative) return <Narrative payload={narrative} />;
      return (
        <p className={`${styles.text} ${role === "user" ? styles.userText : ""}`}>
          {text}
        </p>
      );
    }

    case "reasoning":
      return <ThinkingStream text={String((part.payload as any).text ?? "")} />;

    case "tool_call":
      return <ToolCall payload={part.payload as unknown as ToolCallPayload} />;

    case "tool_result":
      return <ToolResult payload={part.payload as unknown as ToolResultPayload} />;

    case "artifact":
      return <ArtifactCard payload={part.payload as unknown as ArtifactPayload} />;

    case "error":
      return <ErrorPanel payload={part.payload as unknown as ErrorPayload} onRetry={onRetry} />;

    case "card_response": {
      // 7.5：作答后就地坍缩为一行摘要。摘要文案来自后端，前端不自己拼。
      const p = part.payload as unknown as CardResponsePayload;
      return <div className={styles.text} style={{ fontSize: "var(--t-13)", color: "var(--ink-soft)" }}>
        {p.summary}
      </div>;
    }

    // card_request 由 RunView 单独处理（要区分待作答与已作答）
    default:
      return null;
  }
}

function ToolCall({ payload }: { payload: ToolCallPayload }) {
  const [open, setOpen] = useState(false);
  const exploratory = payload.track === "exploratory";

  return (
    <div className={exploratory ? styles.exploratory : styles.trusted}>
      {exploratory ? (
        <span className={styles.exploratoryTag}>探索性执行 · 结果未经校验</span>
      ) : null}
      <div className={styles.toolHead}>
        <span className={styles.toolName}>{payload.name}</span>
        {typeof payload.args?.purpose === "string" ? (
          <span>{payload.args.purpose}</span>
        ) : null}
      </div>
      {/* R9 验收：生成的代码在执行前对用户完整可见，可折叠可复制 */}
      {payload.code ? (
        <>
          <button className={styles.toggle} onClick={() => setOpen((o) => !o)}
                  aria-expanded={open}>
            {open ? "收起代码" : `查看代码（${payload.code.split("\n").length} 行）`}
          </button>
          {open ? <pre className={styles.code}>{payload.code}</pre> : null}
        </>
      ) : null}
    </div>
  );
}

function ToolResult({ payload }: { payload: ToolResultPayload }) {
  const exploratory = payload.track === "exploratory";
  const text =
    typeof payload.result === "string"
      ? payload.result
      : payload.stdout || JSON.stringify(payload.result, null, 2);

  return (
    <div className={exploratory ? styles.exploratory : styles.trusted}>
      {exploratory ? (
        <span className={styles.exploratoryTag}>
          探索性结果 · 不可用于报告中的数字
        </span>
      ) : null}
      <div className={styles.toolHead}>
        <span className={styles.toolName}>{payload.name}</span>
        <span>{payload.ok ? "完成" : "失败"}</span>
      </div>
      {text ? <pre className={styles.stdout}>{text.slice(0, 8000)}</pre> : null}
      {payload.stderr ? (
        <pre className={styles.stdout}>{payload.stderr.slice(0, 4000)}</pre>
      ) : null}
    </div>
  );
}

function ArtifactCard({ payload }: { payload: ArtifactPayload }) {
  return (
    <div className={styles.artifact}>
      <div className={styles.artifactMain}>
        <div className={styles.artifactName}>{payload.filename}</div>
        {/* R6 验收：显示文件名、大小、生成时间、下载按钮 */}
        <div className={`${styles.artifactMeta} num`}>
          {formatBytes(payload.size_bytes)}
        </div>
      </div>
      <div className={styles.artifactActions}>
        {payload.has_preview ? (
          <a className={styles.download}
             href={artifactDownloadUrl(payload.artifact_id, true)}
             target="_blank" rel="noreferrer">预览</a>
        ) : null}
        <a className={styles.download}
           href={artifactDownloadUrl(payload.artifact_id)}>下载</a>
      </div>
    </div>
  );
}

/**
 * 错误面板（7.7）。
 * 「直接说明哪一步、什么原因、下一步怎么办，附日志展开与从此步重试按钮。
 *   错误文案不道歉、不含糊。」
 */
function ErrorPanel({
  payload, onRetry,
}: { payload: ErrorPayload; onRetry?: (stepId: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const detail = (payload.detail ?? {}) as Record<string, unknown>;
  const hint = typeof detail.hint === "string" ? detail.hint : "";
  const log = typeof detail.stderr_tail === "string" ? detail.stderr_tail : "";

  return (
    <div className={styles.error} role="alert">
      {/* 7.7 三段式：哪一步 */}
      <div className={styles.errorWhere}>
        {payload.step_id ? `步骤 ${payload.step_id}` : "执行"}
      </div>

      {/* 什么原因 —— 具体到字段/列名，不是「执行出错」 */}
      <div className={styles.errorWhy}>{payload.message}</div>

      {/* 下一步怎么办 */}
      {hint ? <div className={styles.errorHint}>{hint}</div> : null}

      <div className={styles.errorNext}>
        {payload.retryable && onRetry ? (
          <button className={styles.retry} onClick={() => onRetry(payload.step_id)}>
            从此步重试
          </button>
        ) : null}
        {log ? (
          <button className={styles.toggle} onClick={() => setOpen((o) => !o)}
                  aria-expanded={open}>
            {open ? "收起日志" : "查看日志"}
          </button>
        ) : null}
      </div>

      {open && log ? <pre className={styles.errorDetail}>{log}</pre> : null}
    </div>
  );
}
