"use client";

import { useMotionToken } from "@/lib/motion";
import styles from "./Card.module.css";

/**
 * 卡片外框（7.5「在等我」）：3px 朱批竖条 + 标题旁 2s 呼吸圆点 +
 * 从 spine 生长的入场。
 *
 * 呼吸点要申请运动令牌 —— 它优先级最高（waiting > thinking > acting），
 * 所以卡片一出现，页面上其他持续动画自动让位（7.6 第 2 条）。
 */
export function CardFrame({
  id, title, body, children,
}: {
  id: string; title: string; body?: string; children: React.ReactNode;
}) {
  const animate = useMotionToken(`card:${id}`, "waiting", true);

  return (
    <section className={styles.card} aria-labelledby={`card-title-${id}`}>
      <div className={styles.head}>
        <span className={`${styles.dot} ${animate ? styles.dotBreathe : ""}`} />
        <h2 className={styles.title} id={`card-title-${id}`}>{title}</h2>
      </div>
      {body ? <p className={styles.body}>{body}</p> : null}
      {children}
    </section>
  );
}
