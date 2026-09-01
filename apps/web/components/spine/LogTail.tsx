"use client";

import { useState } from "react";
import styles from "./LogTail.module.css";

/** 日志尾行 + 完整日志抽屉（7.5「在做」）。 */
export function LogTail({ lines }: { lines: string[] }) {
  const [open, setOpen] = useState(false);
  if (lines.length === 0) return null;
  const last = lines[lines.length - 1];

  return (
    <>
      <button
        className={styles.tail}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {open ? "收起日志" : last}
      </button>
      {open ? (
        <pre className={styles.drawer}>{lines.join("\n")}</pre>
      ) : null}
    </>
  );
}
