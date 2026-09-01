"use client";

import { useState } from "react";
import type { CardSpec } from "@/lib/types";
import styles from "./Card.module.css";
import { CardFrame } from "./CardFrame";
import { Actions, Field } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "select" }>;

export function SelectCard({ spec, busy, expiresAt, serverErrors, onSubmit }: CardProps & { spec: Spec }) {
  const [value, setValue] = useState(spec.default ?? spec.options[0] ?? "");
  const [error, setError] = useState("");

  const submit = (action: string) => {
    const cancels = spec.actions.find((a) => a.id === action)?.cancels;
    if (!cancels && !value) { setError("必选"); return; }
    setError("");
    onSubmit(action, { [spec.key]: value });
  };

  return (
    <CardFrame id="select" title={spec.title} body={spec.body}>
      <div className={styles.fields}>
        <Field label="" error={error || serverErrors[spec.key]}>
          <div className={styles.options} role="radiogroup" aria-label={spec.title}>
            {spec.options.map((o) => (
              <label key={o} className={styles.option}>
                <input type="radio" name={spec.key} value={o}
                       checked={value === o} onChange={() => setValue(o)} />
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
