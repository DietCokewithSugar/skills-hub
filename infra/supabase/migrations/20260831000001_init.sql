-- ═══════════════════════════════════════════════════════════════════
-- Bench（工位）· 初始 schema
-- PRD 6.2 数据模型 + 满足 P0 验收所必需的 cards / step_runs
-- 这个文件是 DDL 的唯一来源；SQLAlchemy 模型只是它的映射。
-- ═══════════════════════════════════════════════════════════════════

create extension if not exists "pgcrypto";

-- ── 枚举 ───────────────────────────────────────────────────────────
do $$ begin
  create type run_status as enum
    ('queued','running','waiting_for_input','succeeded','failed','cancelled','expired');
exception when duplicate_object then null; end $$;

do $$ begin
  create type session_status as enum
    ('idle','running','waiting_for_input','failed','expired');
exception when duplicate_object then null; end $$;

do $$ begin
  create type part_type as enum
    ('text','reasoning','tool_call','tool_result',
     'card_request','card_response','artifact','error');
exception when duplicate_object then null; end $$;

do $$ begin
  create type message_role as enum ('user','assistant','system');
exception when duplicate_object then null; end $$;

do $$ begin
  create type step_type as enum ('python','interaction','llm');
exception when duplicate_object then null; end $$;

-- 双轨制（PRD R0.7）：trusted 的结果才允许进正式报告
do $$ begin
  create type track as enum ('trusted','exploratory');
exception when duplicate_object then null; end $$;

do $$ begin
  create type card_status as enum ('pending','answered','expired','cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type step_status as enum
    ('pending','running','waiting_for_input','succeeded','failed','skipped','cancelled');
exception when duplicate_object then null; end $$;

-- ── skills ────────────────────────────────────────────────────────
-- 注册表。id + version 联合主键，会话记录执行时的版本（为 P1-R10 留口）。
create table if not exists skills (
  id            text        not null,
  version       text        not null,
  manifest      jsonb       not null,
  source_ref    text,
  enabled       boolean     not null default true,
  registered_at timestamptz not null default now(),
  primary key (id, version)
);
create index if not exists skills_enabled_idx on skills (enabled) where enabled;

-- ── sessions ──────────────────────────────────────────────────────
-- workspace_id 为 P2-R19 预留，v1 恒等于 user_id。
-- seq_counter 是会话内 Part 的原子取号器，即 SSE 的 Last-Event-ID。
create table if not exists sessions (
  id            uuid           primary key default gen_random_uuid(),
  user_id       uuid           not null,
  workspace_id  uuid           not null,
  skill_id      text,
  title         text           not null default '新会话',
  status        session_status not null default 'idle',
  seq_counter   bigint         not null default 0,
  -- R9：「本会话内不再询问」探索轨确认
  settings      jsonb          not null default '{}'::jsonb,
  created_at    timestamptz    not null default now(),
  updated_at    timestamptz    not null default now(),
  deleted_at    timestamptz
);
create index if not exists sessions_user_updated_idx
  on sessions (user_id, updated_at desc) where deleted_at is null;

-- ── messages ──────────────────────────────────────────────────────
create table if not exists messages (
  id         uuid         primary key default gen_random_uuid(),
  session_id uuid         not null references sessions (id) on delete cascade,
  role       message_role not null,
  created_at timestamptz  not null default now()
);
create index if not exists messages_session_idx on messages (session_id, created_at);

-- ── parts ─────────────────────────────────────────────────────────
-- append-only。seq 在 session 内单调递增且唯一 —— 这是 R2 回放与
-- SSE 断线补发「不重不漏」的全部依据。
create table if not exists parts (
  id         uuid        primary key default gen_random_uuid(),
  message_id uuid        not null references messages (id) on delete cascade,
  session_id uuid        not null references sessions (id) on delete cascade,
  seq        bigint      not null,
  type       part_type   not null,
  payload    jsonb       not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint parts_session_seq_uniq unique (session_id, seq)
);
create index if not exists parts_session_seq_idx on parts (session_id, seq);

-- ── runs ──────────────────────────────────────────────────────────
-- model / prompt_version / skill_version / input_hashes 共同构成
-- R0.6 的复现记录：同一份数据 + 同一版 skill = 同一份报告。
create table if not exists runs (
  id             uuid        primary key default gen_random_uuid(),
  session_id     uuid        not null references sessions (id) on delete cascade,
  skill_id       text,
  skill_version  text,
  status         run_status  not null default 'queued',
  current_step   text,
  sandbox_ref    text,
  started_at     timestamptz,
  ended_at       timestamptz,
  token_in       bigint      not null default 0,
  token_out      bigint      not null default 0,
  error          jsonb,
  model          text,
  prompt_version text,
  input_hashes   jsonb       not null default '{}'::jsonb,
  cancel_requested boolean   not null default false,
  created_at     timestamptz not null default now()
);
create index if not exists runs_session_idx on runs (session_id, created_at desc);
create index if not exists runs_status_idx on runs (status);

-- ── step_runs ─────────────────────────────────────────────────────
-- R8「从失败的 step 重试，而不是整个重跑」需要每步的独立状态与输出。
-- attempt 记录重试次数；track 把双轨制落到 schema 上而不只是 UI 标记。
create table if not exists step_runs (
  id         uuid        primary key default gen_random_uuid(),
  run_id     uuid        not null references runs (id) on delete cascade,
  session_id uuid        not null references sessions (id) on delete cascade,
  step_id    text        not null,
  idx        integer     not null,
  type       step_type   not null,
  track      track       not null default 'trusted',
  status     step_status not null default 'pending',
  attempt    integer     not null default 1,
  started_at timestamptz,
  ended_at   timestamptz,
  output     jsonb,
  error      jsonb,
  created_at timestamptz not null default now()
);
create index if not exists step_runs_run_idx on step_runs (run_id, idx);

-- ── cards ─────────────────────────────────────────────────────────
-- R5 验收：「关掉浏览器隔天再打开，卡片仍在原位可作答」——
-- 卡片必须落库，不能只活在内存里的协程状态中。
create table if not exists cards (
  id          uuid        primary key default gen_random_uuid(),
  run_id      uuid        not null references runs (id) on delete cascade,
  session_id  uuid        not null references sessions (id) on delete cascade,
  step_id     text        not null,
  spec        jsonb       not null,
  status      card_status not null default 'pending',
  response    jsonb,
  request_seq bigint,
  expires_at  timestamptz not null,
  answered_at timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists cards_pending_idx
  on cards (expires_at) where status = 'pending';
create index if not exists cards_run_idx on cards (run_id, created_at);

-- ── artifacts ─────────────────────────────────────────────────────
-- track='exploratory' 的产物永不进入正式产物列表（R9 验收）。
create table if not exists artifacts (
  id          uuid        primary key default gen_random_uuid(),
  session_id  uuid        not null references sessions (id) on delete cascade,
  run_id      uuid        references runs (id) on delete set null,
  filename    text        not null,
  mime        text        not null default 'application/octet-stream',
  storage_key text        not null,
  size_bytes  bigint      not null default 0,
  track       track       not null default 'trusted',
  -- 报告的 PDF 预览版（R6：转换失败时降级为仅 Word，此列为空）
  preview_key text,
  created_at  timestamptz not null default now()
);
create index if not exists artifacts_session_idx on artifacts (session_id, created_at desc);

-- ── updated_at 触发器 ─────────────────────────────────────────────
create or replace function touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists sessions_touch on sessions;
create trigger sessions_touch before update on sessions
  for each row execute function touch_updated_at();
