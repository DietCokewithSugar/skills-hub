# 架构

```
┌─────────────────────────────────────────────┐
│ Next.js (App Router)                        │
│ RSC 拉历史 · EventSource 订阅实时 · 卡片注册表 │
└───────────────┬─────────────────────────────┘
                │ HTTPS + SSE
┌───────────────▼─────────────────────────────┐
│ FastAPI                                     │
│ CRUD · SSE 网关 · 编排循环 · 卡片状态机       │
└──────┬────────────────────────┬─────────────┘
       │ ARQ (Redis)            │ SandboxRunner
┌──────▼──────────┐   ┌─────────▼─────────────┐
│ Worker          │   │ E2B microVM           │
│ 执行 · 产物收集   │   │ 隔离执行 Python        │
└──────┬──────────┘   └───────────────────────┘
       │
┌──────▼──────────────────────────────────────┐
│ Supabase: Postgres(RLS) · Storage            │
└─────────────────────────────────────────────┘
```

---

## 一、Run 是可恢复的状态机，不是长活协程

这是整个后端最重要的一个决定。

遇到 `interaction` step 时，编排器**不等待**：写 `card_request` Part、
把卡片落库、把 Run 置为 `waiting_for_input`，然后抛 `WaitingForInput`
让 worker 任务干净结束。用户作答后 `POST /runs/{id}/resume` 入队一个新任务，
从下一步继续。

三件事因此同时成立：

- 一张等 24 小时的卡片不占 worker 槽位；
- 关掉浏览器、重启服务，卡片仍在原位可作答（R5 验收）；
- 从失败的 step 重试只是换一个 `start_index`（R8），不用整个重跑。

代价是执行上下文不能留在内存里 —— 卡片答案、已完成步骤的输出，
每次调度都从库里读回来（`orchestrator/loop.py::build_context`）。
这是值得的：进程可以随时死掉。

```
queued → running → {waiting_for_input → running}* → succeeded
                 ↘ failed / cancelled / expired
```

---

## 二、SSE 断线补发为什么是那个顺序

`api/sse.py` 的四步顺序不是实现细节，是契约：

1. **先订阅** Redis，把期间到达的事件放进内存缓冲；
2. **再**按 `Last-Event-ID` 从库里补发 `seq >` 的 Part；
3. **然后**吐缓冲里 seq 大于「已补发最大 seq」的事件（去重）；
4. 之后转入纯实时。

反过来（先查库再订阅）会在两步之间丢事件；不去重则会重复。两个错误都违反
R2「不重不漏」。

配套的两条约束：

- **落库先于发布**（`events/emitter.py`）。反过来会出现「客户端收到了
  seq=5，但库里还没有 5」的窗口，重连补发就会漏。
- **Redis 只负责「现在」**，Postgres 的 `parts` 表负责「历史」。
  Redis 丢一条不影响正确性，因为重连是从库里读的。

`seq` 由 `UPDATE sessions SET seq_counter = seq_counter + 1 RETURNING`
原子取号，表上还有 `unique(session_id, seq)` 兜底 —— 写重了直接报错，
而不是悄悄错序。

---

## 三、瞬时事件 vs 落库的 Part

| 类型 | 例子 | 落库 | 带 seq | 参与补发 |
|---|---|---|---|---|
| Part | text / reasoning / artifact / error / card_* | 是 | 是 | 是 |
| 瞬时事件 | step.progress / step.log / part.delta / heartbeat | 否 | 否 | 否 |

补发一条 30 秒前的进度百分比没有意义，也不该顶掉 `Last-Event-ID`。

**但这带来一个坑**：`step.started/completed` 是瞬时的，光靠它们刷新页面后
工序时间轴就没了。所以步骤状态另有持久化（`step_runs` 表），前端在
`GET /runs/{id}` 里读回来。每个 Part 上还盖了执行时的 `step_id`
（`events/emitter.py`），这样回放时内容仍然挂在正确的工序下。

---

## 四、双轨制落在 schema 上

`track: trusted | exploratory` 是 `step_runs`、`tool_call`/`tool_result`
Part、`artifacts` 上的一等字段，不只是 UI 标记。

| | 可信轨 | 探索轨 |
|---|---|---|
| 代码来源 | 仓库中审核过的 skill 步骤 | 模型现场生成 |
| 能否进正式报告 | 是 | **否** |
| 写入目录 | `/output` | `/workspace/scratch/` |
| 沙箱要求 | 任意 runner | **必须内核级隔离** |

守住这条线的四个机制：

1. `LocalSubprocessRunner` 对 `track="exploratory"` 直接抛
   `UntrustedCodeRefused` —— 结构性拒绝，不是配置项；
2. 产物收集器只看 `/output`，`scratch` 在 runner 层就没进来；
3. 生成的代码全文落 `tool_call` Part，执行前对用户完整可见；
4. 结果 Part 标 `exploratory`，前端用虚线边框 + 明示标签区隔，
   不与正式结果混排。

---

## 五、用户隔离

v1 不做认证。后端以 service role 连 Supabase，**RLS 被绕过**，
所以隔离在应用层强制：`db/repo/base.py` 让每个 repo 持有 `user_id`，
每个查询都经过 `_scope_by_session()`。

RLS 策略已经写好并启用（`infra/supabase/migrations/20260831000002_rls.sql`），
接上 Auth 后把连接切到 `authenticated` 角色并透传 JWT，即刻成为第二道闸门，
业务代码不用改。

`tests/integration/test_isolation.py` 直接构造别人的 id 去打每一个 repo 方法，
确认一个都读不到。

---

## 六、留给 P2 的口子

| 需求 | 已留的口子 |
|---|---|
| R15 沙箱暂停/恢复 | `SandboxRunner` Protocol，换实现即可 |
| R16 私有化 / 内网沙箱 | 执行层与存储层都是 Protocol，不与供应商 SDK 耦合 |
| R17 多客户端 | 业务逻辑全在服务端，OpenAPI 可生成 SDK |
| R18 脚本内实时提问 | 沙箱契约里留了 stdout JSONL 通道，v1 不开启 |
| R19 团队空间 | 所有表带 `workspace_id`，v1 恒等于 `user_id` |
