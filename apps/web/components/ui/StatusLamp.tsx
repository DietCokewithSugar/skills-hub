"use client";

import styles from "./StatusLamp.module.css";

export type LampState = "pending" | "running" | "succeeded" | "waiting" | "failed";

const LABEL: Record<LampState, string> = {
  pending: "未开始",
  running: "进行中",
  succeeded: "已完成",
  waiting: "等待你确认",
  failed: "失败",
};

/**
 * 状态灯。7.2：「状态不能只靠颜色传达，同时用形状与文字」——
 * 所以形状随状态变（空心 / 半环 / 实心 / 方块），并且带 aria-label。
 *
 * animate 由调用方根据运动令牌传入，本组件不自己申请令牌。
 */
export function StatusLamp({
  state, animate = false,
}: { state: LampState; animate?: boolean }) {
  const motion =
    animate && state === "running" ? styles.spin :
    animate && state === "waiting" ? styles.breathe : "";
  return (
    <span
      className={`${styles.lamp} ${styles[state]} ${motion}`}
      role="img"
      aria-label={LABEL[state]}
    />
  );
}
