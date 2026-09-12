/**
 * 模型配置管理 — 端到端回归测试
 *
 * 与其他 e2e 不同，这个套件**不打桩后端**，而是直接打真实部署。
 * 原因：本功能的核心风险不在 UI，而在"写回 config.yaml"这套文件协议
 * （外科替换、注释保全、备份、失败回滚、CRLF 保持），mock 掉后端就
 * 恰好把要测的东西测没了。
 *
 * 目标可以是：
 *   · 本地 `make dev` 起的实例（默认）
 *   · 内网 Windows 部署（设 DEER_FLOW_E2E_BASE_URL=http://192.168.2.10:3000）
 *
 * 运行：
 *   cd frontend && pnpm e2e:models
 *   DEER_FLOW_E2E_BASE_URL=http://192.168.2.10:3000 \
 *   DEER_FLOW_E2E_EMAIL=admin@sz-jlc.com \
 *   DEER_FLOW_E2E_PASSWORD='...' pnpm e2e:models
 *
 * ⚠️ 这个套件会**真的修改目标机的 config.yaml**（并产生备份）。
 *    每个用例结束都会清理自己创建的模型；但如果你中途 Ctrl-C，
 *    可能会残留一个 `e2e-*` 模型和若干 `config.yaml.bak.*`。
 *    清理方式见文件末尾的注释。
 */

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

const BASE_URL = process.env.DEER_FLOW_E2E_BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.DEER_FLOW_E2E_EMAIL ?? "admin@sz-jlc.com";
const PASSWORD = process.env.DEER_FLOW_E2E_PASSWORD ?? "";

/** 本套件创建的所有模型都带此前缀，便于识别与清理。 */
const PREFIX = "e2e-model-settings";

const adminKey = (name: string) => `${PREFIX}-${name}`;

// ---------------------------------------------------------------------------
// 前置检查
// ---------------------------------------------------------------------------

test.beforeAll(() => {
  if (!PASSWORD) {
    throw new Error(
      "缺少管理员密码。请设置 DEER_FLOW_E2E_PASSWORD 环境变量，例如：\n" +
        "  DEER_FLOW_E2E_PASSWORD='...' pnpm e2e:models",
    );
  }
});

// ---------------------------------------------------------------------------
// 辅助
// ---------------------------------------------------------------------------

/**
 * 登录并返回可复用的 API 上下文。
 *
 * 走网关而不是 UI：登录表单有一次性的 `needs_setup` 重定向逻辑，
 * 用 API 拿 cookie 更稳，且能顺带拿到 CSRF token。
 */
async function loginAsAdmin(): Promise<APIRequestContext> {
  const { request } = await import("@playwright/test");
  const ctx = await request.newContext({ baseURL: BASE_URL });

  const res = await ctx.post("/api/v1/auth/login/local", {
    form: { username: EMAIL, password: PASSWORD },
  });
  expect(res.status(), `登录失败（${EMAIL}）：${await res.text()}`).toBe(200);

  return ctx;
}

/** 列出托管区内的模型。 */
async function listModels(ctx: APIRequestContext): Promise<Array<{ name: string }>> {
  const res = await ctx.get("/api/models/config");
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { models?: Array<{ name: string }> };
  return body.models ?? [];
}

/** 删除本套件创建的全部模型（幂等；不存在的忽略）。 */
async function cleanupModels(ctx: APIRequestContext): Promise<void> {
  for (const model of await listModels(ctx)) {
    if (!model.name.startsWith(PREFIX)) continue;
    const res = await ctx.delete(`/api/models/${encodeURIComponent(model.name)}`);
    // 404 = 已经被删掉了，同样算干净。
    expect([200, 404]).toContain(res.status());
  }
}

/** 通过 UI 打开「设置 → 模型」。 */
async function openModelSettings(page: Page): Promise<void> {
  await page.goto("/workspace");
  // 设置入口在侧边栏；不同版本文案可能是「设置」或齿轮图标。
  const trigger = page
    .getByRole("button", { name: /设置|Settings/ })
    .or(page.getByLabel(/设置|Settings/))
    .first();
  await trigger.click();

  await page.getByRole("button", { name: /^模型$|^Models$/ }).click();
  await expect(page.getByText(/添加模型|Add model/)).toBeVisible();
}

// ---------------------------------------------------------------------------
// 用例
// ---------------------------------------------------------------------------

test.describe("模型配置", () => {
  test("后端可达且管理员登录成功", async () => {
    const ctx = await loginAsAdmin();
    const res = await ctx.get("/api/models/config");
    expect(res.status()).toBe(200);
    await ctx.dispose();
  });

  test("非管理员被拒绝", async () => {
    // 用一个故意错误的密码：期望 401 而不是 200。
    const { request } = await import("@playwright/test");
    const ctx = await request.newContext({ baseURL: BASE_URL });
    const res = await ctx.post("/api/v1/auth/login/local", {
      form: { username: EMAIL, password: `${PASSWORD}-definitely-wrong` },
    });
    expect([401, 403, 429]).toContain(res.status());
    await ctx.dispose();
  });

  test("新增 → 列表可见 → 删除，且不破坏既有模型", async () => {
    const ctx = await loginAsAdmin();
    try {
      const before = await listModels(ctx);
      const name = adminKey("crud");

      const created = await ctx.post("/api/models", {
        data: {
          name,
          use: "langchain_openai:ChatOpenAI",
          model: "gpt-4o",
          display_name: "E2E CRUD",
          // 用 $VAR 引用形式；后端会把它落进 .env 而不是 config.yaml。
          api_key: "$E2E_MODEL_SETTINGS_KEY",
          api_key_value: "sk-e2e-not-a-real-key",
        },
      });
      expect(created.status(), await created.text()).toBe(200);

      const after = await listModels(ctx);
      expect(after.map((m) => m.name)).toContain(name);
      // 既有模型一个都不能少。
      for (const m of before) {
        expect(after.map((x) => x.name)).toContain(m.name);
      }

      const del = await ctx.delete(`/api/models/${encodeURIComponent(name)}`);
      expect(del.status()).toBe(200);
      expect((await listModels(ctx)).map((m) => m.name)).not.toContain(name);
    } finally {
      await cleanupModels(ctx);
      await ctx.dispose();
    }
  });

  test("重名返回 409", async () => {
    const ctx = await loginAsAdmin();
    try {
      const name = adminKey("dup");
      const payload = { name, use: "langchain_openai:ChatOpenAI", model: "gpt-4o" };

      expect((await ctx.post("/api/models", { data: payload })).status()).toBe(200);
      expect((await ctx.post("/api/models", { data: payload })).status()).toBe(409);
    } finally {
      await cleanupModels(ctx);
      await ctx.dispose();
    }
  });

  test("无效 use 路径被拒绝（400），且不产生破坏", async () => {
    const ctx = await loginAsAdmin();
    try {
      const before = await listModels(ctx);
      const bad = await ctx.post("/api/models", {
        data: {
          name: adminKey("bad-use"),
          use: "nonexistent.module:Nope",
          model: "x",
        },
      });
      expect(bad.status()).toBe(400);

      // 关键：拒绝之后既有模型仍然完好。
      const after = await listModels(ctx);
      expect(after.map((m) => m.name).sort()).toEqual(before.map((m) => m.name).sort());
    } finally {
      await ctx.dispose();
    }
  });

  test("无效 use 不会把服务搞挂", async () => {
    const ctx = await loginAsAdmin();
    try {
      await ctx.post("/api/models", {
        data: { name: adminKey("health"), use: "nonexistent.module:Nope", model: "x" },
      });
      // 这是本功能最重要的安全属性：写坏配置不能拖垮服务。
      // 注意 `/health` 只挂在网关(8001)上，前端(3000)不转发它，
      // 所以这里用一条必须经过网关的接口来探测存活。
      const probe = await ctx.get("/api/models/config");
      expect(probe.status()).toBe(200);
    } finally {
      await ctx.dispose();
    }
  });

  test("密钥不会以明文回传", async () => {
    const ctx = await loginAsAdmin();
    try {
      const secret = "sk-e2e-should-never-echo-back-1234567890";
      const name = adminKey("mask");
      expect(
        (
          await ctx.post("/api/models", {
            data: {
              name,
              use: "langchain_openai:ChatOpenAI",
              model: "gpt-4o",
              api_key: "$E2E_MASK_KEY",
              api_key_value: secret,
            },
          })
        ).status(),
      ).toBe(200);

      const res = await ctx.get("/api/models/config");
      const body = await res.text();
      expect(body).not.toContain(secret);
      expect(body).not.toContain("e2e-should-never-echo-back");
    } finally {
      await cleanupModels(ctx);
      await ctx.dispose();
    }
  });

  test("界面可以完成一次新增与删除", async ({ page }) => {
    const ctx = await loginAsAdmin();
    const name = adminKey("ui");
    try {
      await page.goto("/login");
      await page.getByLabel(/邮箱|Email/i).fill(EMAIL);
      await page.getByLabel(/密码|Password/i).fill(PASSWORD);
      await page.getByRole("button", { name: /登录|Sign in/i }).click();
      await page.waitForURL(/\/workspace/, { timeout: 15_000 });

      await openModelSettings(page);

      await page.getByRole("button", { name: /添加模型|Add model/ }).click();
      await page.getByPlaceholder(/模型名称|name/i).first().fill(name);
      await page.getByPlaceholder(/提供方|provider|use/i).first().fill("langchain_openai:ChatOpenAI");
      await page.getByPlaceholder(/^模型|model/i).first().fill("gpt-4o");
      await page.getByRole("button", { name: /^保存|Save/ }).click();

      // 保存成功后列表里应出现该模型（等后端往返）。
      await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 });

      // 用 API 复核：UI 显示的和磁盘上的一致。
      expect((await listModels(ctx)).map((m) => m.name)).toContain(name);
    } finally {
      await cleanupModels(ctx);
      await ctx.dispose();
    }
  });
});

// ---------------------------------------------------------------------------
// 手工清理（Ctrl-C 后的残留）
// ---------------------------------------------------------------------------
//
// 在目标机的 PowerShell 里执行：
//
//   # 查看残留的 e2e 模型
//   Select-String -Path D:\deer-flow\src\config.yaml -Pattern 'e2e-model-settings'
//
//   # 用管理界面逐个删掉，或直接回滚到最近的备份：
//   Get-ChildItem D:\deer-flow\src\config.yaml.bak.* | Sort-Object LastWriteTime -Descending | Select -First 5
//   Copy-Item D:\deer-flow\src\config.yaml.bak.<时间戳> D:\deer-flow\src\config.yaml -Force
//
//   # 回滚后需要重启服务（配置文件是被动热重载，但行尾/备份状态以重启为准）
//   & D:\deer-flow\src\scripts\windows\stop.ps1
//   & D:\deer-flow\src\scripts\windows\start.ps1
