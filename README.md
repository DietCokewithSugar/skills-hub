# Bench（工位）

> 一个网页版的 opencode：会话隔离、可执行 Python skill、能和你对话确认、
> 最终交付 Word 报告。

业务同事把方法论写成了 skill，但它们只能跑在个人电脑上——跑完看不到产物、
执行是黑箱、换个人就跑不起来。缺的不是模型能力，是一个**能把「一次执行」
完整承载下来的运行环境**：谁跑的、跑到哪一步、中间问了什么、产出了什么文件，
全部可见、可追溯、可重来。

平台的第一属性是**结果可信**。当准确性与性能、成本、开发速度冲突时，
一律让位于准确性。

---

## 快速开始

```bash
# 1. 依赖
cp .env.example .env          # 填 Supabase / DeepSeek / E2B 凭据
docker compose up -d redis    # 或 redis-server --daemonize yes

# 2. 建表（Supabase 控制台的 SQL Editor 里按文件名顺序执行，
#    或用 psql 直连；注意 00000000000000_local_auth_stub.sql 只在本地跑）
psql "$SUPABASE_DB_URL" -f infra/supabase/migrations/20260831000001_init.sql
psql "$SUPABASE_DB_URL" -f infra/supabase/migrations/20260831000002_rls.sql

# 3. 后端
cd apps/api
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/uvicorn bench.main:app --reload            # API
.venv/bin/arq bench.worker.tasks.WorkerSettings      # worker

# 4. 前端
cd apps/web && npm install && npm run dev
```

打开 http://localhost:3000 。

**没有云凭据也能跑**：不配 Supabase 时产物落本地文件系统（契约相同，
仍走签名 URL）；`BENCH_SANDBOX=local` 用本地子进程跑可信 skill
（无隔离，仅限自测，且会硬拒绝模型生成的代码）。

---

## 仓库结构

```
apps/api/          FastAPI + ARQ worker
apps/web/          Next.js（第七章设计规范）
skills/            skill 仓库：hello-world、ux-report
sandbox/runtime/   沙箱内的 bench SDK（bench.stats / bench.io / bench.asserts）
sandbox/e2b.Dockerfile
infra/supabase/migrations/
docs/              架构、skill 编写、准确性、运维
```

---

## 验证

```bash
cd apps/api
.venv/bin/python -m pytest -q                                   # 90 passed
BENCH_SANDBOX=local .venv/bin/python -m bench.accuracy.fixtures run --repeat 5

cd apps/web
npm run typecheck && npm run build
npm run e2e                                                     # 需要整栈起着
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 分层、Run 状态机、SSE 补发、双轨制 |
| [docs/accuracy.md](docs/accuracy.md) | R0 四道闸门各自守什么 |
| [docs/skill-authoring.md](docs/skill-authoring.md) | 怎么写一个 skill |
| [docs/runbook.md](docs/runbook.md) | 部署、E2B template、已知限制 |
