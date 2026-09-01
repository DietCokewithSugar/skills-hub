"use client";

import { useEffect, useState } from "react";
import { formatTime, relativeTime } from "@/lib/format";

/**
 * 相对时间（「3 分钟前」）。
 *
 * 这类值天生依赖 Date.now()，服务端与客户端渲染必然算出不同的字符串，
 * 触发 hydration mismatch。suppressHydrationWarning 正是为时间戳这种
 * 场景准备的：告诉 React 这段文本两边不一致是预期的。
 *
 * 挂载后每 30 秒刷新一次，列表放着不动也不会一直显示「刚刚」。
 */
export function RelativeTime({ iso }: { iso: string }) {
  const [, tick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <time dateTime={iso} title={formatTime(iso)} suppressHydrationWarning>
      {relativeTime(iso)}
    </time>
  );
}
