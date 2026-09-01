/**
 * 失败路径（7.7）：执行失败要说清哪一步、什么原因、下一步怎么办。
 * 用缺列的输入触发 R0.4 的计算前拦截。
 */
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs";

const WEB = "http://127.0.0.1:3000";
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const BAD_CSV = path.join(REPO, "skills/ux-report/fixtures/missing_column/input/raw.csv");
const SHOTS = path.join(REPO, "artifacts/screenshots");

let failures = 0;
const check = (c, l) => { c ? console.log(`✓ ${l}`) : (failures++, console.log(`✗ ${l}`)); };

const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium", args: ["--no-sandbox"] });
const page = await b.newPage({ viewport: { width: 1440, height: 900 } });
fs.mkdirSync(SHOTS, { recursive: true });

try {
  await page.goto(WEB, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /体验评测报告/ }).click();
  await page.waitForURL(/\/sessions\//, { timeout: 20000 });

  // 缺 scope 列的输入
  await page.setInputFiles('input[type="file"]', BAD_CSV);
  await page.waitForSelector("text=已上传 raw.csv", { timeout: 20000 });
  await page.getByRole("button", { name: "开始执行" }).click();

  await page.getByRole("heading", { name: "确认评测范围" })
    .waitFor({ timeout: 60000 });
  await page.locator('input[type="number"]').first().fill("30");
  await page.getByRole("button", { name: "确认并继续" }).click();

  // R0.4：在计算前被拦下，并指名缺哪一列
  // 用内容定位，别用 .first() —— 卡片的字段错误也带 role="alert"
  const alert = page.locator('[role="alert"]').filter({ hasText: "步骤" }).first();
  await alert.waitFor({ timeout: 90000 });
  await page.waitForTimeout(300);
  const text = await alert.innerText();

  check(/scope/.test(text), `错误指名了缺失的列（scope）`);
  check(/步骤|compute/.test(text), "错误说明了是哪一步");
  // 「下一步怎么办」可以是重试按钮，也可以是一句可执行的指引 ——
  // 缺列这种情况重试同一份文件毫无意义，给指引才是对的。
  const hasRetry = await page.getByRole("button", { name: "从此步重试" }).count() > 0;
  const hasHint = /换一份|调整|联系/.test(text);
  check(hasRetry || hasHint,
        `给出了「下一步怎么办」（${hasRetry ? "重试按钮" : "文字指引"}）`);
  check(!/抱歉|对不起|出错了，请稍后/.test(text), "错误文案不道歉、不含糊");
  await page.screenshot({ path: path.join(SHOTS, "08-error.png"), fullPage: true });
  console.log(`\n  错误面板正文：${text.replace(/\n/g, " ").slice(0, 120)}`);

  // 没有产出报告（R0 验收：不产出报告）
  const dl = await page.locator('a:has-text("下载")').count();
  check(dl === 0, "拦截后没有产出任何报告");
} catch (e) {
  failures++;
  console.log(`✗ 异常：${e.message}`);
  await page.screenshot({ path: path.join(SHOTS, "98-error-failure.png") }).catch(() => {});
} finally {
  await b.close();
}
console.log(failures === 0 ? "\n失败路径全部通过" : `\n${failures} 项未通过`);
process.exit(failures ? 1 : 0);
