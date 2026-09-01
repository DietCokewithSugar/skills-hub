"use client";

import type { CardSpec } from "@/lib/types";
import { CardFrame } from "./CardFrame";
import { Actions } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "confirm" }>;

export function ConfirmCard({ spec, busy, expiresAt, onSubmit }: CardProps & { spec: Spec }) {
  return (
    <CardFrame id="confirm" title={spec.title} body={spec.body}>
      <Actions actions={spec.actions} busy={busy} expiresAt={expiresAt}
               onSubmit={(a) => onSubmit(a, {})} />
    </CardFrame>
  );
}
