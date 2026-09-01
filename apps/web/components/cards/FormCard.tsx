"use client";

import { useState } from "react";
import type { CardSpec, FormField } from "@/lib/types";
import styles from "./Card.module.css";
import { CardFrame } from "./CardFrame";
import { Actions, Field } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "form" }>;

/**
 * 前端校验，规则与后端 validate_answer() 保持一致。
 *
 * R5 验收：「非法输入（低于 min、必填为空）在前端拦截并给出字段级错误」。
 * 但这只是体验层 —— 后端会再校验一次，因为前端可以被绕过。
 */
function validate(fields: FormField[], values: Record<string, unknown>) {
  const errors: Record<string, string> = {};
  for (const f of fields) {
    const raw = values[f.key];
    const empty = raw == null || (typeof raw === "string" && raw.trim() === "");
    if (empty) {
      if (f.required) errors[f.key] = "必填";
      continue;
    }
    if (f.type === "number") {
      const n = Number(raw);
      if (Number.isNaN(n)) { errors[f.key] = "必须是数字"; continue; }
      if (f.min != null && n < f.min) { errors[f.key] = `不能小于 ${f.min}`; continue; }
      if (f.max != null && n > f.max) { errors[f.key] = `不能大于 ${f.max}`; continue; }
    }
    if (f.type === "select" && !f.options.includes(String(raw))) {
      errors[f.key] = "不在可选项中";
    }
  }
  return errors;
}

export function FormCard({ spec, busy, expiresAt, serverErrors, onSubmit }: CardProps & { spec: Spec }) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(
      spec.fields.map((f) => [f.key, f.default ?? (f.type === "checkbox" ? false : "")]),
    ),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});

  const set = (k: string, v: unknown) => setValues((s) => ({ ...s, [k]: v }));

  const submit = (action: string) => {
    if (spec.actions.find((a) => a.id === action)?.cancels) {
      onSubmit(action, {}); return;
    }
    const e = validate(spec.fields, values);
    setErrors(e);
    if (Object.keys(e).length > 0) return;
    onSubmit(action, values);
  };

  return (
    <CardFrame id="form" title={spec.title} body={spec.body}>
      <div className={styles.fields}>
        {spec.fields.map((f) => {
          const err = errors[f.key] || serverErrors[f.key];
          const cls = `${styles.input} ${err ? styles.inputError : ""}`;
          return (
            <Field key={f.key} label={f.label} help={f.help} error={err}>
              {f.type === "select" ? (
                <select className={`${styles.select} ${err ? styles.inputError : ""}`}
                        value={String(values[f.key] ?? "")}
                        onChange={(e) => set(f.key, e.target.value)}>
                  {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : f.type === "textarea" ? (
                <textarea className={`${styles.textarea} ${err ? styles.inputError : ""}`}
                          value={String(values[f.key] ?? "")}
                          placeholder={f.placeholder}
                          onChange={(e) => set(f.key, e.target.value)} />
              ) : f.type === "checkbox" ? (
                <label className={styles.option}>
                  <input type="checkbox" checked={Boolean(values[f.key])}
                         onChange={(e) => set(f.key, e.target.checked)} />
                  {f.placeholder || "是"}
                </label>
              ) : (
                <input
                  className={cls}
                  type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
                  value={String(values[f.key] ?? "")}
                  placeholder={f.placeholder}
                  min={f.min ?? undefined}
                  max={f.max ?? undefined}
                  aria-invalid={Boolean(err)}
                  onChange={(e) => set(f.key, e.target.value)}
                />
              )}
            </Field>
          );
        })}
      </div>
      <Actions actions={spec.actions} busy={busy} expiresAt={expiresAt} onSubmit={submit} />
    </CardFrame>
  );
}
