"use client";

import { useEffect, useState } from "react";
import { formatElapsed } from "@/lib/format";
import { useReducedMotion } from "@/lib/motion";

/**
 * 计时器。R7 验收：「计时器显示真实经过时间，不是估算」——
 * 从 step 的真实起止时间戳算差值，100ms 刷新（7.5「在做」）。
 *
 * 关于首帧：服务端渲染和客户端首次渲染必须得到**同一个值**，否则 React
 * 会报 hydration mismatch（服务端算出 00:04、客户端算出 00:05）。
 * 所以首帧一律按 startedAt 算（即 00:00），挂载后再切到真实的 now。
 * 已结束的步骤本来就是确定值，不受影响。
 */
export function Timer({
  startedAt, endedAt,
}: { startedAt: number | null; endedAt: number | null }) {
  const reduced = useReducedMotion();
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    if (startedAt == null || endedAt != null) return;
    setNow(Date.now());
    // reduced-motion 下放慢到 1s —— 仍是真实时间，只是少一点视觉抖动
    const id = setInterval(() => setNow(Date.now()), reduced ? 1000 : 100);
    return () => clearInterval(id);
  }, [startedAt, endedAt, reduced]);

  if (startedAt == null) return null;
  const end = endedAt ?? now ?? startedAt;
  const elapsed = end - startedAt;

  return (
    <time className="num" dateTime={`PT${Math.round(elapsed / 1000)}S`}>
      {formatElapsed(elapsed)}
    </time>
  );
}
