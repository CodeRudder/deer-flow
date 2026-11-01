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

/**
 * 托管区里的一条模型记录。
 *
 * 索引签名是刻意的：`config.yaml` 的条目会带任意服务商专属字段
 * （`when_thinking_enabled` / `max_tokens` / `api_base` ...），断言
 * 「某键存在/不存在」时不该先把它强转成别的类型。
 */
interface ManagedModelEntry {
  name: string;
  [key: string]: unknown;
}

/** 列出托管区内的模型。 */
async function listModels(ctx: APIRequestContext): Promise<ManagedModelEntry[]> {
  const res = await ctx.get("/api/models/config");
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { models?: ManagedModelEntry[] };
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
  // Sidebar footer button opens a dropdown; one of its items is "Settings".
  await page.getByRole("button", { name: /Settings and more/ }).click();
  await page.getByRole("menuitem", { name: /^Settings$/ }).click();
  // The dialog lists sections; "Models" is the one under test.
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add model" })).toBeVisible();
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

  test("服务商表下发思考模板，且形态按服务商分叉", async () => {
    const ctx = await loginAsAdmin();
    try {
      const res = await ctx.get("/api/models/providers");
      expect(res.status()).toBe(200);
      const body = (await res.json()) as {
        providers: Array<Record<string, unknown>>;
      };
      const byKey = new Map(body.providers.map((p) => [p.key as string, p]));

      // 每个 service 的思考参数写法不同，不能被统一成一种形状——这正是
      // 界面不能自己拼参数、必须由预设表下发的原因。
      const anthropic = byKey.get("anthropic");
      expect(anthropic?.thinking_enabled).toEqual({
        thinking: { type: "enabled", budget_tokens: 4096 },
      });
      expect(anthropic?.thinking_needs_budget).toBe(true);

      const compatible = byKey.get("openai-compatible");
      expect(compatible?.thinking_enabled).toEqual({
        extra_body: { thinking: { type: "enabled" } },
      });
      // 不传 budget 的服务商不该拿到 budget 字段（ModelConfig extra="allow"
      // 会把未知键直接透传给 SDK）。
      expect(compatible?.thinking_needs_budget).toBe(false);

      const google = byKey.get("google");
      expect(google?.thinking_enabled).toEqual({ thinking_budget: 4096 });

      // 每个支持思考的服务商都必须同时给出「关」的写法。
      for (const p of body.providers) {
        if (p.supports_thinking !== true) continue;
        expect(p.thinking_enabled, `${String(p.key)} 缺少启用模板`).toBeTruthy();
        expect(p.thinking_disabled, `${String(p.key)} 缺少关闭模板`).toBeTruthy();
      }
    } finally {
      await ctx.dispose();
    }
  });

  test("思考配置能写进 config.yaml 并原样回读", async () => {
    const ctx = await loginAsAdmin();
    const name = adminKey("thinking");
    try {
      // 这就是界面上勾选「该模型支持思考」后发出的载荷。
      const payload = {
        name,
        use: "langchain_anthropic:ChatAnthropic",
        model: "claude-sonnet-5",
        supports_thinking: true,
        when_thinking_enabled: {
          thinking: { type: "enabled", budget_tokens: 2048 },
        },
        when_thinking_disabled: { thinking: { type: "disabled" } },
        max_tokens: 8192,
        api_key: "$E2E_THINKING_KEY",
        api_key_value: "sk-e2e-not-a-real-key",
      };
      const created = await ctx.post("/api/models", { data: payload });
      expect(created.status(), await created.text()).toBe(200);

      // Playwright 的 expect 不做类型收窄，所以显式抛一次。
      const entry =
        (await listModels(ctx)).find((m) => m.name === name) ?? null;
      if (!entry) throw new Error(`${name} 保存后未出现在托管区`);
      expect(entry.supports_thinking).toBe(true);
      expect(entry.when_thinking_enabled).toEqual({
        thinking: { type: "enabled", budget_tokens: 2048 },
      });
      expect(entry.when_thinking_disabled).toEqual({
        thinking: { type: "disabled" },
      });
      expect(entry.max_tokens).toBe(8192);

      // 关掉思考 = PUT 一个不带这三个键的载荷（界面 uncheck 后就是这么发的）。
      // `supports_thinking` 是 DeerFlow 真正读取的开关，留着就等于没关掉。
      const updated = await ctx.put(`/api/models/${encodeURIComponent(name)}`, {
        data: { name, use: "langchain_anthropic:ChatAnthropic", model: "claude-sonnet-5" },
      });
      expect(updated.status(), await updated.text()).toBe(200);

      const after = (await listModels(ctx)).find((m) => m.name === name) ?? null;
      if (!after) throw new Error(`${name} 在更新后消失了`);
      expect(after).not.toHaveProperty("supports_thinking");
      expect(after).not.toHaveProperty("when_thinking_enabled");
      expect(after).not.toHaveProperty("when_thinking_disabled");
      expect(after).not.toHaveProperty("max_tokens");
    } finally {
      await cleanupModels(ctx);
      await ctx.dispose();
    }
  });

  test("探测端点不写任何文件，坏端点返回业务结果而不是 500", async () => {
    const ctx = await loginAsAdmin();
    try {
      const before = JSON.stringify(await listModels(ctx));

      // 指向一个必然连不上的端点：探测必须把失败当成一条业务结论
      // 返回（ok:false + error），而不是抛 500 —— 管理界面上那是
      // 一次正常作答，不是服务器故障。
      const res = await ctx.post("/api/models/probe-thinking", {
        data: {
          name: adminKey("probe"),
          use: "langchain_openai:ChatOpenAI",
          model: "does-not-exist",
          openai_api_base: "http://127.0.0.1:1/v1",
          api_key: "$E2E_PROBE_KEY",
        },
      });
      expect(res.status(), await res.text()).toBe(200);
      const body = (await res.json()) as {
        ok: boolean;
        respects_enabled: boolean;
        respects_disabled: boolean;
        thinks_by_default: boolean;
        error: string | null;
      };
      expect(body.ok).toBe(false);
      expect(body.error).toBeTruthy();
      // 探测失败时三个结论都必须是 false —— 它们表示「未知」，不是「否」。
      expect(body.respects_enabled).toBe(false);
      expect(body.respects_disabled).toBe(false);
      expect(body.thinks_by_default).toBe(false);

      // 探测是只读的：托管区一个字节都不能变。
      expect(JSON.stringify(await listModels(ctx))).toBe(before);
    } finally {
      await ctx.dispose();
    }
  });

  test("探测端点需要管理员", async () => {
    const ctx = await loginAsAdmin();
    try {
      // 未带会话 cookie 的裸请求应被拒，且不能因为探测而落盘。
      const res = await ctx.post("/api/models/probe-thinking", {
        data: {
          name: adminKey("anon"),
          use: "langchain_openai:ChatOpenAI",
          model: "gpt-4o",
        },
        headers: { Cookie: "", "X-CSRF-Token": "" },
      });
      expect([401, 403, 422]).toContain(res.status());
      expect(res.status()).not.toBe(200);
    } finally {
      await ctx.dispose();
    }
  });

  test("界面可以完成一次新增与删除", async ({ page }) => {
    const ctx = await loginAsAdmin();
    const name = adminKey("ui");
    try {
      // The deployment runs the English locale, so selectors use the en-US
      // strings (src/core/i18n/locales/en-US.ts).
      await page.goto("/login");
      await page.getByLabel("Email").fill(EMAIL);
      await page.getByLabel("Password").fill(PASSWORD);
      await page.getByRole("button", { name: "Sign In" }).click();
      await page.waitForURL(/\/workspace/, { timeout: 20_000 });

      await openModelSettings(page);

      await page.getByRole("button", { name: "Add model" }).click();
      // 服务商是下拉框，不是文本框 —— 用户看不到也填不了类路径。
      await page.locator("#model-provider").click();
      await page.getByRole("option", { name: /OpenAI/ }).first().click();
      // 表单在对话框内；用 label 精确定位，避免与页面上其它 gpt-4o 占位符冲突。
      // 表单里的 Field 未把 label 与 input 关联，输入框的可访问名来自
      // placeholder；页面上另有同名占位符（模型菜单等），故用 exact 锚定。
      await page.getByPlaceholder("e.g. gpt-4o").fill(name);
      await page.getByPlaceholder("gpt-4o", { exact: true }).fill("gpt-4o");
      await page.getByRole("button", { name: "Save" }).click();

      // 保存成功后列表里应出现该模型（等后端往返）。
      // 用 first()：表单在关闭前也会回显同一个名称。
      await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 });

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
