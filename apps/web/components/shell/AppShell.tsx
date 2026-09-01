"use client";

import { useState } from "react";
import styles from "./AppShell.module.css";

export function AppShell({
  rail, children, workspace,
}: {
  rail: React.ReactNode;
  children: React.ReactNode;
  workspace?: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className={styles.shell}>
      <nav className={styles.rail} aria-label="会话列表">{rail}</nav>
      <main className={styles.main}>{children}</main>
      {workspace ? (
        <aside
          className={`${styles.workspace} ${collapsed ? styles.workspaceCollapsed : ""}`}
          aria-label="工作区"
          aria-hidden={collapsed}
        >
          {workspace}
        </aside>
      ) : null}
      {workspace ? (
        <button className="sr-only" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? "展开工作区" : "收起工作区"}
        </button>
      ) : null}
    </div>
  );
}
