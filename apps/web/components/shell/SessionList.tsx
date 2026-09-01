"use client";

import Link from "next/link";
import type { SessionStatus, SessionSummary } from "@/lib/types";
import { RelativeTime } from "../ui/RelativeTime";
import { StatusLamp, type LampState } from "../ui/StatusLamp";
import styles from "./SessionList.module.css";

const LAMP: Record<SessionStatus, LampState> = {
  idle: "succeeded",
  running: "running",
  waiting_for_input: "waiting",
  failed: "failed",
  expired: "failed",
};

/** R1：会话列表按最近更新排序，显示标题、所属 skill、状态点、相对时间。 */
export function SessionList({
  sessions, activeId,
}: { sessions: SessionSummary[]; activeId?: string }) {
  return (
    <>
      <div className={styles.head}>
        <span className={styles.brand}>工位</span>
        <Link href="/" className={styles.new}>新建</Link>
      </div>
      <div className={styles.list}>
        {sessions.length === 0 ? (
          <p className={styles.empty}>还没有会话</p>
        ) : (
          sessions.map((s) => (
            <Link
              key={s.id}
              href={`/sessions/${s.id}`}
              className={`${styles.item} ${s.id === activeId ? styles.itemActive : ""}`}
              aria-current={s.id === activeId ? "page" : undefined}
            >
              <div className={styles.itemHead}>
                <StatusLamp state={LAMP[s.status] ?? "pending"} />
                <span className={styles.title}>{s.title}</span>
              </div>
              <div className={styles.meta}>
                <span>{s.skill_id ?? "未绑定"}</span>
                <span>·</span>
                <RelativeTime iso={s.updated_at} />
              </div>
            </Link>
          ))
        )}
      </div>
    </>
  );
}
