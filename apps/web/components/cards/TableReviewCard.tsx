"use client";

import { useState } from "react";
import type { CardSpec } from "@/lib/types";
import styles from "./Card.module.css";
import { CardFrame } from "./CardFrame";
import { Actions } from "./fields";
import type { CardProps } from "./registry";

type Spec = Extract<CardSpec, { type: "table_review" }>;

/** 表格数据审阅，支持逐行接受/剔除。 */
export function TableReviewCard({ spec, busy, expiresAt, onSubmit }: CardProps & { spec: Spec }) {
  const ids = spec.rows.map((r, i) => (r[spec.row_id_key] ?? i) as string | number);
  const [accepted, setAccepted] = useState<Set<string | number>>(
    () => new Set(spec.default_accepted ? ids : []),
  );

  const toggle = (id: string | number) =>
    setAccepted((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const submit = (action: string) => {
    if (spec.actions.find((a) => a.id === action)?.cancels) {
      onSubmit(action, {}); return;
    }
    onSubmit(action, { [spec.key]: [...accepted] });
  };

  return (
    <CardFrame id="table" title={spec.title} body={spec.body}>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">采用</th>
              {spec.columns.map((c) => <th key={c} scope="col">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {spec.rows.map((row, i) => {
              const id = ids[i];
              const on = accepted.has(id);
              return (
                <tr key={String(id)} className={on ? "" : styles.rowOut}>
                  <td>
                    <input type="checkbox" checked={on} onChange={() => toggle(id)}
                           aria-label={`第 ${i + 1} 行`} />
                  </td>
                  {spec.columns.map((c) => (
                    <td key={c} className="num">{String(row[c] ?? "")}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className={styles.fieldHelp}>
        已采用 <span className="num">{accepted.size}</span> / {spec.rows.length} 行
      </p>
      <Actions actions={spec.actions} busy={busy} expiresAt={expiresAt} onSubmit={submit} />
    </CardFrame>
  );
}
