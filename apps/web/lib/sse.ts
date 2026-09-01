"use client";

/**
 * SSE 订阅。
 *
 * 浏览器原生 EventSource 会在断线后自动重连，**并自动带上 Last-Event-ID
 * 头**（值来自服务端发的 `id:` 行）。后端据此从 seq 之后补发。
 * 所以 R2 的「断网 30 秒后补齐」不需要前端写任何重连逻辑 ——
 * 只要我们别自己去 new 一个新的 EventSource 把 Last-Event-ID 丢掉。
 */

import { useEffect, useRef } from "react";
import { sseUrl } from "./api";
import type { BenchEvent, EventType } from "./types";

const EVENT_TYPES: EventType[] = [
  "run.started", "run.completed", "run.failed", "run.cancelled",
  "run.waiting", "run.resumed", "run.expired",
  "part.created", "part.delta", "part.completed",
  "step.started", "step.progress", "step.completed", "step.failed", "step.log",
  "card.requested", "card.answered", "card.expired",
  "artifact.created", "usage.updated", "heartbeat",
];

export function useEventStream(
  sessionId: string,
  afterSeq: number,
  onEvent: (ev: BenchEvent) => void,
  onConnectionChange?: (connected: boolean) => void,
) {
  // 用 ref 存回调，避免回调变化导致重连（重连会丢 Last-Event-ID）
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const connChange = useRef(onConnectionChange);
  connChange.current = onConnectionChange;

  useEffect(() => {
    if (!sessionId) return;
    const es = new EventSource(sseUrl(sessionId, afterSeq));

    const dispatch = (type: EventType) => (e: MessageEvent) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      // e.lastEventId 就是服务端发的 id: 行，即 Part 的 seq
      const seq = e.lastEventId ? Number(e.lastEventId) : null;
      handler.current({
        type,
        seq: Number.isFinite(seq as number) ? (seq as number) : null,
        data,
      });
    };

    const listeners = EVENT_TYPES.map((t) => {
      const fn = dispatch(t);
      es.addEventListener(t, fn as EventListener);
      return [t, fn] as const;
    });

    es.onopen = () => connChange.current?.(true);
    es.onerror = () => {
      // EventSource 会自己重连并带上 Last-Event-ID，这里只更新连接指示
      connChange.current?.(false);
    };

    return () => {
      listeners.forEach(([t, fn]) => es.removeEventListener(t, fn as EventListener));
      es.close();
    };
    // afterSeq 只在挂载时用一次；它变化不该触发重连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
}
