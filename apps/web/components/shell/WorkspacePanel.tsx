"use client";

import { artifactDownloadUrl } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { ArtifactInfo } from "@/lib/types";
import styles from "./WorkspacePanel.module.css";

/**
 * 工作区栏：会话产物一览。
 *
 * 只列 track='trusted' 的正式产物 —— 后端 list_artifacts 默认就这么过滤，
 * 探索轨的东西进不来（R9 验收）。
 */
export function WorkspacePanel({ artifacts }: { artifacts: ArtifactInfo[] }) {
  return (
    <>
      <div className={styles.head}>工作区</div>
      <div className={styles.list}>
        {artifacts.length === 0 ? (
          <p className={styles.empty}>还没有产物</p>
        ) : (
          artifacts.map((a) => (
            <a key={a.id} className={styles.file} href={artifactDownloadUrl(a.id)}>
              <span>{a.filename}</span>
              <span className={`${styles.size} num`}>{formatBytes(a.size_bytes)}</span>
            </a>
          ))
        )}
      </div>
    </>
  );
}
