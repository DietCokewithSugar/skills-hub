-- ═══════════════════════════════════════════════════════════════════
-- 仅供本地/CI 用的 Supabase 兼容垫片。
--
-- Supabase 托管库自带 auth schema 与 auth.uid()/auth.role()；本地起的
-- 裸 Postgres 没有，RLS 迁移会因缺函数而失败。这个文件补上同签名的空实现，
-- 让同一套迁移能在本地跑通并被测试验证。
--
-- **不要在 Supabase 云上执行这个文件**（会覆盖真实实现）。
-- 迁移执行顺序按文件名排序，全零前缀保证它最先跑。
-- ═══════════════════════════════════════════════════════════════════

create schema if not exists auth;

-- 本地无 JWT，返回 request.jwt.claim.sub（测试可用 set_config 注入）
create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

create or replace function auth.role() returns text
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.role', true), ''), 'authenticated')
$$;
