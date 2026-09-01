import styles from "./ProgressBar.module.css";

/**
 * 进度条。只在拿到 stdout JSONL 的真实百分比时渲染 ——
 * 7.6 第 1 条：「动效只表达真实状态，不做装饰性 loading」。
 * pct 为 null 就什么都不画，而不是画一个滚动的假进度。
 */
export function ProgressBar({ pct, label }: { pct: number | null; label?: string }) {
  if (pct == null) return null;
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div
      className={styles.track}
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label ?? "执行进度"}
    >
      <div className={styles.fill} style={{ width: `${clamped}%` }} />
    </div>
  );
}
