/**
 * 浏览器冒烟：走一遍 PRD 的主路径。
 *
 * 覆盖：空状态 → 选 skill → 传文件 → 已受理 → 卡片作答 →
 *       产物出现 → 刷新页面回放一致。
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const WEB = process.env.WEB_BASE ?? "http://127.0.0.1:3000";
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const CSV = path.join(REPO, "skills/ux-report/fixtures/basic/input/raw.csv");
const SHOTS = path.join(REPO, "artifacts/screenshots");

const log = (m) => console.log(m);
let failures = 0;
function check(cond, label) {
  if (cond) log(`✓ ${label}`);
  else { failures += 1; log(`✗ ${label}`); }
}

const browser = await chromium.launch({
  executablePath: "/opt/pw-browsers/chromium",
  args: ["--no-sandbox"],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
fs.mkdirSync(SHOTS, { recursive: true });

const errors = [];
const http4xx = [];
page.on("response", (r) => { if (r.status() >= 400) http4xx.push(`${r.status()} ${r.request().method()} ${r.url()}`); });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

try {
  // ── 1. 空状态（7.7：空屏是行动的入口）──
  await page.goto(WEB, { waitUntil: "networkidle" });
  check(await page.getByText("选一个 skill 开始").isVisible(), "空状态给出行动入口，而不是插图");
  check(await page.getByText("体验评测报告").first().isVisible(), "可用 skill 列出");
  await page.screenshot({ path: path.join(SHOTS, "01-empty-state.png") });

  // ── 2. 选 skill 建会话 ──
  await page.getByRole("button", { name: /体验评测报告/ }).click();
  await page.waitForURL(/\/sessions\//, { timeout: 15000 });
  check(true, `会话已建：${new URL(page.url()).pathname}`);

  // ── 3. 传输入文件 ──
  await page.setInputFiles('input[type="file"]', CSV);
  await page.waitForSelector("text=已上传 raw.csv", { timeout: 20000 });
  check(true, "输入文件已上传");
  await page.screenshot({ path: path.join(SHOTS, "02-session-start.png") });

  // ── 4. 提交（R7：立即「已受理」）──
  await page.getByRole("button", { name: "开始执行" }).click();

  // ── 5. 卡片出现（R5）──
  await page.getByRole("heading", { name: "确认评测范围" })
    .waitFor({ timeout: 60000 });
  check(true, "确认卡片已出现");
  await page.waitForTimeout(400);   // 等 opacity 过渡走完再量，否则量到中间值
  const dim = await page.evaluate(() => {
    const el = [...document.querySelectorAll("div")]
      .find((d) => d.className && /dimmed/.test(d.className));
    return el ? getComputedStyle(el).opacity : null;
  });
  check(dim !== null && Math.abs(Number(dim) - 0.4) < 0.01,
        `「在等我」时执行流降到 40% 不透明度（实测 ${dim}）`);
  check((await page.title()).startsWith("●"), "浏览器标签页带 ● 前缀，能看出在等你");
  await page.screenshot({ path: path.join(SHOTS, "03-waiting-card.png") });

  // ── 6. 前端字段级校验（R5 验收）──
  const sample = page.locator('input[type="number"]').first();
  await sample.fill("5");
  await page.getByRole("button", { name: "确认并继续" }).click();
  await page.waitForSelector("text=不能小于 30", { timeout: 10000 });
  check(true, "低于 min 的输入被前端拦下并给出字段级错误");
  await page.screenshot({ path: path.join(SHOTS, "04-field-error.png") });

  // ── 7. 合法作答 ──
  await sample.fill("200");
  await page.getByRole("button", { name: "确认并继续" }).click();

  // ── 8. 产物出现 ──
  await page.waitForSelector("text=体验评测报告", { timeout: 120000 });
  await page.waitForSelector('a:has-text("下载")', { timeout: 120000 });
  check(true, "产物卡片出现在消息流中，带下载入口");
  await page.screenshot({ path: path.join(SHOTS, "05-artifact.png"), fullPage: true });

  // 摘要：卡片作答后坍缩为一行
  check(await page.getByText(/已确认/).first().isVisible(),
        "卡片作答后坍缩为一行摘要");

  // ── 9. 刷新回放（R2 验收）──
  const before = await page.locator("body").innerText();
  // 注意：会话页常驻一条 SSE 连接，网络永远不会 idle。
  // 任何针对本应用的自动化都不能用 networkidle 等会话页。
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('a:has-text("下载")', { timeout: 30000 });
  const after = await page.locator("body").innerText();
  const keep = (t) => ["已确认", "下载", "体验评测报告"].filter((k) => t.includes(k));
  check(keep(after).length === keep(before).length && keep(after).length === 3,
        "刷新后卡片答案、产物链接全部还原");
  check(!(await page.title()).startsWith("●"), "执行结束后标签页 ● 前缀消失");
  await page.screenshot({ path: path.join(SHOTS, "06-after-reload.png"), fullPage: true });

  // ── 10. 无障碍与动效纪律 ──
  const live = await page.locator('[aria-live="polite"]').count();
  check(live > 0, "存在 aria-live 区域供读屏用户获知状态变化");

  const moving = await page.evaluate(() => {
    let n = 0;
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (s.animationName && s.animationName !== "none" &&
          s.animationIterationCount === "infinite") n += 1;
    }
    return n;
  });
  check(moving <= 1, `同一时刻持续运动的元素不超过一个（实测 ${moving}）`);

  // ── 11. 移动端只读（7.8：响应式至 768px）──
  await page.setViewportSize({ width: 375, height: 800 });
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 2, `375px 下无横向溢出（实测 ${overflow}px）`);
  await page.screenshot({ path: path.join(SHOTS, "07-mobile.png"), fullPage: true });

  if (http4xx.length) console.log("  [debug] 4xx/5xx:", [...new Set(http4xx)].join("\n              "));
  check(errors.length === 0, `无 JS 控制台错误${errors.length ? "：" + errors.slice(0, 3).join(" | ") : ""}`);
} catch (e) {
  failures += 1;
  log(`✗ 异常：${e.message}`);
  await page.screenshot({ path: path.join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

log(failures === 0 ? "\n浏览器冒烟全部通过" : `\n${failures} 项未通过`);
process.exit(failures === 0 ? 0 : 1);
