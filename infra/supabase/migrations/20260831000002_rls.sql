-- ═══════════════════════════════════════════════════════════════════
-- 行级安全（PRD R1：「所有表带 user_id，策略 auth.uid() = user_id」）
--
-- 当前状态：v1 不做认证，后端用 Supabase 直连串以 service role 连接，
-- 该角色带 BYPASSRLS，因此下面的策略此刻**不会生效**。
-- 用户隔离目前由应用层强制：bench/db/repo/ 下每个查询都带
-- `WHERE user_id = :current_user`，见 bench/db/repo/base.py。
--
-- 接上 Supabase Auth 后，把后端连接切到 anon/authenticated 角色并透传
-- 用户 JWT，这些策略即刻成为第二道闸门，无需改业务代码。
-- ═══════════════════════════════════════════════════════════════════

alter table sessions  enable row level security;
alter table messages  enable row level security;
alter table parts     enable row level security;
alter table runs      enable row level security;
alter table step_runs enable row level security;
alter table cards     enable row level security;
alter table artifacts enable row level security;
alter table skills    enable row level security;

-- sessions 是所有权的根：其余表通过 session_id 回溯到 user_id。
drop policy if exists sessions_owner on sessions;
create policy sessions_owner on sessions
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 子表统一用一个 exists 子查询挂到 sessions 上。
do $$
declare t text;
begin
  foreach t in array array['messages','parts','runs','step_runs','cards','artifacts']
  loop
    execute format('drop policy if exists %I_owner on %I', t, t);
    execute format($f$
      create policy %I_owner on %I for all
        using (exists (select 1 from sessions s
                        where s.id = %I.session_id and s.user_id = auth.uid()))
        with check (exists (select 1 from sessions s
                             where s.id = %I.session_id and s.user_id = auth.uid()))
    $f$, t, t, t, t);
  end loop;
end $$;

-- skills 是全局注册表：所有登录用户可读，只有 service role 可写。
drop policy if exists skills_read on skills;
create policy skills_read on skills for select using (auth.role() = 'authenticated');
