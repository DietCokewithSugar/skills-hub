"use client";

/**
 * 动效协调器 —— PRD 7.6 第 2 条：
 * 「同一时刻页面上只有一个持续运动的元素。新的运动开始前，旧的必须停止。」
 *
 * 靠人工约定守不住这条（每加一个组件就多一次犯错机会），所以做成一个
 * **唯一的令牌**：想做持续动画的组件先申请，拿不到就渲染静态形态。
 * 令牌按优先级抢占 —— 「在等我」比「在做」重要，朱批呼吸点应该盖过
 * 半环旋转。
 *
 * R7 验收：「任何时刻页面上正在运动的元素不超过一个」。
 */

import {
  createContext, useCallback, useContext, useEffect,
  useMemo, useRef, useState,
} from "react";

/** 数字越大越优先。同优先级先到先得。 */
export const MOTION_PRIORITY = {
  acting: 1,    // 半环旋转
  thinking: 2,  // spine 靛蓝渐变
  waiting: 3,   // 朱批呼吸点
} as const;

export type MotionKind = keyof typeof MOTION_PRIORITY;

interface Claim { id: string; kind: MotionKind }

interface MotionCtx {
  claim: (id: string, kind: MotionKind) => void;
  release: (id: string) => void;
  holder: string | null;
  reducedMotion: boolean;
}

const Ctx = createContext<MotionCtx>({
  claim: () => {}, release: () => {}, holder: null, reducedMotion: false,
});

export function MotionProvider({ children }: { children: React.ReactNode }) {
  const [holder, setHolder] = useState<string | null>(null);
  const claims = useRef<Map<string, Claim>>(new Map());
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const recompute = useCallback(() => {
    let best: Claim | null = null;
    for (const c of claims.current.values()) {
      if (!best || MOTION_PRIORITY[c.kind] > MOTION_PRIORITY[best.kind]) best = c;
    }
    setHolder(best?.id ?? null);
  }, []);

  const claim = useCallback((id: string, kind: MotionKind) => {
    claims.current.set(id, { id, kind });
    recompute();
  }, [recompute]);

  const release = useCallback((id: string) => {
    claims.current.delete(id);
    recompute();
  }, [recompute]);

  const value = useMemo(
    () => ({ claim, release, holder, reducedMotion }),
    [claim, release, holder, reducedMotion],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * 申请运动令牌。返回是否拿到 —— 没拿到就渲染静态形态。
 * reduced-motion 下永远返回 false，所有循环动画降级为静态状态标记（7.6 第 4 条）。
 */
export function useMotionToken(id: string, kind: MotionKind, active: boolean): boolean {
  const { claim, release, holder, reducedMotion } = useContext(Ctx);

  useEffect(() => {
    if (active && !reducedMotion) {
      claim(id, kind);
      return () => release(id);
    }
    release(id);
    return undefined;
  }, [id, kind, active, reducedMotion, claim, release]);

  return active && !reducedMotion && holder === id;
}

export const useReducedMotion = () => useContext(Ctx).reducedMotion;
