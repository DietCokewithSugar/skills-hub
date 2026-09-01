"use client";

import styles from "./Card.module.css";

export function Field({
  label, help, error, children,
}: {
  label: string; help?: string; error?: string; children: React.ReactNode;
}) {
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel}>{label}</label>
      {children}
      {help ? <span className={styles.fieldHelp}>{help}</span> : null}
      {/* R5 验收：字段级错误 */}
      {error ? <span className={styles.error} role="alert">{error}</span> : null}
    </div>
  );
}

export function Actions({
  actions, onSubmit, busy, expiresAt, disabled,
}: {
  actions: { id: string; label: string; primary: boolean; cancels: boolean }[];
  onSubmit: (actionId: string) => void;
  busy: boolean;
  expiresAt?: string;
  disabled?: boolean;
}) {
  return (
    <div className={styles.actions}>
      {actions.map((a) => (
        <button
          key={a.id}
          className={`${styles.btn} ${a.primary ? styles.btnPrimary : ""}`}
          onClick={() => onSubmit(a.id)}
          disabled={busy || (disabled && !a.cancels)}
        >
          {busy && a.primary ? "提交中…" : a.label}
        </button>
      ))}
      {expiresAt ? (
        <span className={styles.expiry}>
          {formatExpiry(expiresAt)}
        </span>
      ) : null}
    </div>
  );
}

function formatExpiry(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return "";
  if (ms <= 0) return "已超时";
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours} 小时后挂起`;
  return `${Math.max(1, Math.floor(ms / 60_000))} 分钟后挂起`;
}
