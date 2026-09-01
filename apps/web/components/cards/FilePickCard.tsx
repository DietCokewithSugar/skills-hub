"use client";

import { useState } from "react";
import type { CardSpec } from "@/lib/types";
import styles from "./Card.module.css";
import { CardFrame } from "./CardFrame";
import { Actions, Field } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "file_pick" }>;

/** 从工作区已有文件中选择 —— 候选由后端在发卡时用工作区实况填入。 */
export function FilePickCard({ spec, busy, expiresAt, serverErrors, onSubmit }: CardProps & { spec: Spec }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState("");

  const toggle = (name: string) => {
    setPicked((p) =>
      spec.multiple
        ? (p.includes(name) ? p.filter((x) => x !== name) : [...p, name])
        : [name],
    );
  };

  const submit = (action: string) => {
    if (spec.actions.find((a) => a.id === action)?.cancels) {
      onSubmit(action, {}); return;
    }
    if (picked.length === 0) { setError("请选择文件"); return; }
    setError("");
    onSubmit(action, { [spec.key]: spec.multiple ? picked : picked[0] });
  };

  return (
    <CardFrame id="filepick" title={spec.title} body={spec.body}>
      <div className={styles.fields}>
        <Field label="" error={error || serverErrors[spec.key]}>
          {spec.candidates.length === 0 ? (
            <span className={styles.fieldHelp}>工作区里还没有可选的文件</span>
          ) : (
            <div className={styles.options} role="group" aria-label={spec.title}>
              {spec.candidates.map((c) => (
                <label key={c} className={styles.option}>
                  <input type={spec.multiple ? "checkbox" : "radio"}
                         name={spec.key} checked={picked.includes(c)}
                         onChange={() => toggle(c)} />
                  <span className="num">{c}</span>
                </label>
              ))}
            </div>
          )}
        </Field>
      </div>
      <Actions actions={spec.actions} busy={busy} expiresAt={expiresAt}
               onSubmit={submit} disabled={spec.candidates.length === 0} />
    </CardFrame>
  );
}
