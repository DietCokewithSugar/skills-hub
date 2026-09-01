"use client";

import { useState } from "react";
import type { CardSpec } from "@/lib/types";
import styles from "./Card.module.css";
import { CardFrame } from "./CardFrame";
import { Actions, Field } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "multi_select" }>;

export function MultiSelectCard({ spec, busy, expiresAt, serverErrors, onSubmit }: CardProps & { spec: Spec }) {
  const [picked, setPicked] = useState<string[]>(spec.default ?? []);
  const [error, setError] = useState("");

  const toggle = (o: string) =>
    setPicked((p) => (p.includes(o) ? p.filter((x) => x !== o) : [...p, o]));

  const submit = (action: string) => {
    if (spec.actions.find((a) => a.id === action)?.cancels) {
      onSubmit(action, {}); return;
    }
    // 前端拦一道；后端还会再拦一道（前端可以被绕过）
    if (picked.length < spec.min_selected) {
      setError(`至少选择 ${spec.min_selected} 项`); return;
    }
    if (spec.max_selected != null && picked.length > spec.max_selected) {
      setError(`最多选择 ${spec.max_selected} 项`); return;
    }
    setError("");
    onSubmit(action, { [spec.key]: picked });
  };

  return (
    <CardFrame id="multi" title={spec.title} body={spec.body}>
      <div className={styles.fields}>
        <Field label="" error={error || serverErrors[spec.key]}>
          <div className={styles.options} role="group" aria-label={spec.title}>
            {spec.options.map((o) => (
              <label key={o} className={styles.option}>
                <input type="checkbox" checked={picked.includes(o)}
                       onChange={() => toggle(o)} />
                {o}
              </label>
            ))}
          </div>
        </Field>
      </div>
      <Actions actions={spec.actions} busy={busy} expiresAt={expiresAt} onSubmit={submit} />
    </CardFrame>
  );
}
