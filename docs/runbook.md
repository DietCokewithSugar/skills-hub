# 运维手册

## 一、环境变量

见 `.env.example`。几个容易配错的：

| 变量 | 说明 |
|---|---|
| `BENCH_DATABASE_URL` | Supabase 连接串，**必须**用 `postgresql+asyncpg://` 前缀 |
| `BENCH_SANDBOX` | `e2b`（生产）或 `local`（仅 CI/自测，无隔离） |
| `BENCH_CORS_ORIGINS` | `localhost` 与 `127.0.0.1` 是两个不同的 origin，都要列 |
| `BENCH_SKILLS_DIR` | skill 仓库路径 |

启动时会自检并打印告警（`config.audit_startup`），也在 `GET /health` 里返回。
**不要忽略这些告警** —— 它们说的都是「这个配置下某个安全承诺不成立」。

---

## 二、E2B 沙箱

### 构建 template

```bash
e2b template build -c sandbox/e2b.Dockerfile -n bench-python-312
```

镜像里钉死了依赖版本并预装了 `bench` SDK。运行时**禁止** `pip install` ——
既是安全要求，也是复现要求。

### 出网

出网**不是**环境变量控制的。`sandbox/e2b_runner.py` 建沙箱时默认传
`allow_internet_access=False`；要放行必须在 skill.yaml 里显式写域名：

```yaml
limits:
  network_allowlist: [api.internal.example.com]
```

翻译成 `network={"allow_out": [...], "deny_out": ["0.0.0.0/0"]}`。

探索轨（模型生成的代码）**永远无出网**，不看 skill 的白名单。

### 上线前必须跑一次

```bash
cd apps/api && E2B_API_KEY=... pytest tests/security/test_no_egress.py -v
```

这组测试**真的去连外网**然后断言连不上 —— 不是断言某个配置字段等于 False。
配置对不对不重要，网通不通才重要。没配 key 时会跳过，
**跳过不等于通过**。

---

## 三、已知限制

### PDF 预览需要 libreoffice-writer

R6 要求 Word 之外同时生成 PDF 供网页预览。转换用无头 LibreOffice，
但**只装 `libreoffice-core` 是不够的** —— 没有 Writer 过滤器就加载不了
`.docx`，报 `source file could not be loaded`。

```bash
apt-get install -y libreoffice-writer
```

没装时的行为是**降级而不是失败**：报告照常产出，只是没有 PDF 预览，
日志里会打印一条明确的告警。这符合 R6「转换失败时降级为仅提供 Word 下载，
不阻塞整个执行」。

> 本项目的开发环境缺这个包且 apt 源不可用，因此 **PDF 成功路径未经实测**；
> 降级路径已验证。部署后请手动确认一次。

### 认证未做

v1 不做认证，后端以 service role 连库，RLS 被绕过。
用户隔离由 repo 层强制（见 architecture.md 第五节）。
接 Supabase Auth 时：把连接切到 `authenticated` 角色并透传 JWT，
`api/deps.py::current_user` 换成验 JWT 取 sub。业务代码不用改。

### 本地对象存储

没配 `BENCH_SUPABASE_SERVICE_KEY` 时产物落本地文件系统
（`storage/local.py`）。它刻意保持与 Supabase 相同的契约（带过期的签名 URL、
仍走 302），避免「本地跑通了、上云又是另一套行为」。
**不要在生产用它** —— 没有任何跨机器可用性。

---

## 四、定时任务

worker 里有两个 cron（`worker/tasks.py`）：

| 任务 | 频率 | 作用 |
|---|---|---|
| `sweep_cards` | 每 10 分钟 | 把超时未作答的卡片置 expired，Run 置 expired |
| `cleanup_deleted_sessions` | 每天 03:17 | 清理软删除超过保留期的会话产物 |

worker 不跑 = 卡片永远不会超时挂起，用户故事 11 不成立。

---

## 五、排查

```bash
curl localhost:8000/health              # 配置自检与告警
curl localhost:8000/api/skills/errors   # 加载失败的 skill 与行号级报错
```

**skill 列表少了一个** → 看 `/api/skills/errors`。一个 skill 写错了不会拖垮
整张表，但它自己会消失。

**提交后没反应** → 检查 worker 是否在跑、Redis 是否连得上。
API 连不上队列时返回 503，不会假装成功。

**SSE 没有事件** → 检查反向代理是否缓冲了 `text/event-stream`。
响应已带 `X-Accel-Buffering: no`，nginx 之外的代理可能需要另外配。

**执行卡在 waiting_for_input** → 正常。卡片在库里等人作答，
`GET /api/runs/{id}/card` 能取到。超过 `timeout_hours` 由 cron 置 expired。

---

## 六、一条命令起本地栈

```bash
redis-server --daemonize yes
cd apps/api
BENCH_SANDBOX=local .venv/bin/uvicorn bench.main:app --reload &
BENCH_SANDBOX=local .venv/bin/arq bench.worker.tasks.WorkerSettings &
cd ../web && npm run dev
```

`BENCH_SANDBOX=local` 只跑可信 skill，模型生成的代码会被硬拒绝。
