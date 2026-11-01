import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement } from "react";

import MobileAboutSettingsPage from "@/app/m/(app)/(tabbed)/settings/about/page";
import MobileAppearanceSettingsPage from "@/app/m/(app)/(tabbed)/settings/appearance/page";
import MobileChannelsSettingsPage from "@/app/m/(app)/(tabbed)/settings/channels/page";
import MobileModelsSettingsPage from "@/app/m/(app)/(tabbed)/settings/models/page";
import MobileNotificationSettingsPage from "@/app/m/(app)/(tabbed)/settings/notification/page";
import MobileSettingsPage from "@/app/m/(app)/(tabbed)/settings/page";
import MobileSkillsSettingsPage from "@/app/m/(app)/(tabbed)/settings/skills/page";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile settings root and its sub-screens
 * (prototype ⑦, T22 — S1–S6, S8, S9).
 *
 * Three properties are pinned here because a grouped list of drill-downs can
 * get each of them wrong while looking right:
 *
 * 1. **Completeness** — every section the feature list keeps is a row, in the
 *    prototype's four groups, and the removed one (S7, MCP tools) has no row at
 *    all rather than a disabled one.
 * 2. **Read-only is visible before the tap** — the two lists that can only be
 *    read on mobile (S5 models, S8 skills) carry the badge on their *row*, and
 *    the sections behind them offer no write control. A read-only row without
 *    its badge is the specific mistake the prototype's annotation warns about.
 * 3. **Public paths only** — every href is a `/settings/...` public path, so
 *    the middleware — not the component — decides which tree renders and `/m/`
 *    can never reach the address bar (plan §1.2.1).
 *
 * Strings come from the real `en-US` translations rather than a stub, so a
 * missing key fails here instead of rendering `undefined`. The four group
 * headings and the read-only badge are the exception: they are the keys the
 * locale files do not carry yet (`settings-copy.ts` documents why), so their
 * expected text is written out here — if the wording there changes, this file
 * is where that shows up.
 */

const mockAuth = rs.hoisted(() => ({
  current: {
    user: null as { email?: string } | null,
    logout: rs.fn(),
  },
}));

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => mockAuth.current,
}));

/** `next-themes` needs a provider to hold state; the call itself is asserted. */
const mockTheme = rs.hoisted(() => ({ setTheme: rs.fn() }));

rs.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: mockTheme.setTheme }),
}));

// The API layer stays real; only the modules that resolve env/CSRF at import
// time are stubbed, so the URLs asserted below are the ones the hooks build.
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/api/fetcher", () => ({
  fetch: (input: RequestInfo, init?: RequestInit) =>
    globalThis.fetch(input, init),
  getCsrfHeaders: () => ({}),
  readCsrfCookie: () => null,
  isStateChangingMethod: () => false,
}));

const MANAGED_MODELS = {
  models: [
    {
      index: 0,
      name: "gpt-4o",
      model: "gpt-4o",
      display_name: "GPT-4o",
      description: "General purpose model.",
      use: "langchain_openai:ChatOpenAI",
    },
    {
      index: 1,
      name: "deepseek-chat",
      model: "deepseek-chat",
      use: "langchain_deepseek:ChatDeepSeek",
    },
  ],
};

const SKILLS = {
  skills: [
    {
      name: "data-analysis",
      description: "Analyze structured data.",
      category: "public",
      enabled: true,
    },
    {
      name: "frontend-design",
      description: "Create polished frontend interfaces.",
      category: "public",
      enabled: false,
    },
    {
      name: "team-runbook",
      description: "Our own runbook helper.",
      category: "custom",
      enabled: true,
    },
  ],
};

const CHANNEL_PROVIDERS = {
  enabled: true,
  providers: [
    {
      provider: "telegram",
      display_name: "Telegram",
      enabled: true,
      configured: true,
      auth_mode: "deep_link",
      connection_status: "connected",
      credential_fields: [],
    },
    {
      provider: "slack",
      display_name: "Slack",
      enabled: true,
      configured: true,
      auth_mode: "oauth",
      connection_status: "not_connected",
      credential_fields: [],
    },
  ],
};

const CHANNEL_CONNECTIONS = {
  connections: [
    {
      id: "conn-1",
      provider: "telegram",
      status: "connected",
      external_account_name: "deer-bot",
      scopes: [],
      metadata: {},
    },
  ],
};

const fetchCalls: { url: string; init?: RequestInit }[] = [];

type Route = { match: string; body: unknown; status?: number };

function stubBackend(routes: Route[]) {
  rs.stubGlobal(
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      fetchCalls.push({ url, init });
      const route = routes.find((candidate) => url.includes(candidate.match));
      const status = route?.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => route?.body ?? {},
      } as Response;
    },
  );
}

const ALL_ROUTES: Route[] = [
  { match: "/api/models/config", body: MANAGED_MODELS },
  { match: "/api/skills", body: SKILLS },
  { match: "/api/channels/providers", body: CHANNEL_PROVIDERS },
  { match: "/api/channels/connections", body: CHANNEL_CONNECTIONS },
];

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  fetchCalls.length = 0;
  mockAuth.current = { user: null, logout: rs.fn() };
  // `changeLocale` writes the locale cookie on purpose, and jsdom keeps
  // document state for the whole file — without this the language test would
  // leave every later test rendering in Chinese.
  document.cookie = "locale=; path=/; max-age=0";
});

function renderInProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale="en-US">{ui}</I18nProvider>
    </QueryClientProvider>,
  );
}

/** The row's own link, so a badge or value can be attributed to one section. */
function rowLink(testId: string) {
  const link = screen.getByTestId(testId).closest("a");
  if (!link) {
    throw new Error(`${testId} is not inside a link`);
  }
  return link;
}

/** The sub-screen's back link, which is its only way up (tab bar aside). */
function expectPublicBackLink() {
  const back = screen.getByTestId("mobile-settings-back");
  expect(back.getAttribute("href")).toBe("/settings");
}

const SECTION_ROWS = [
  ["mobile-settings-row-account", "Account", "/settings/account"],
  ["mobile-settings-row-appearance", "Appearance", "/settings/appearance"],
  [
    "mobile-settings-row-notification",
    "Notification",
    "/settings/notification",
  ],
  ["mobile-settings-row-memory", "Memory", "/settings/memory"],
  ["mobile-settings-row-channels", "Channels", "/settings/channels"],
  ["mobile-settings-row-models", "Models", "/settings/models"],
  ["mobile-settings-row-skills", "Skills", "/settings/skills"],
  ["mobile-settings-row-about", "About", "/settings/about"],
] as const;

describe("Mobile settings root", () => {
  test("lists every retained section under the prototype's four groups", () => {
    renderInProviders(<MobileSettingsPage />);

    for (const group of [
      "Personal",
      "Connections",
      "Models & capabilities",
      "Other",
    ]) {
      expect(screen.getByRole("heading", { name: group })).toBeTruthy();
    }

    for (const [testId, label] of SECTION_ROWS) {
      const link = rowLink(testId);
      expect(link.textContent).toContain(label);
    }
  });

  test("MCP tools (S7) has no entry at all", () => {
    renderInProviders(<MobileSettingsPage />);

    // Removed, not disabled: an administrator-only configuration screen has no
    // business advertising itself on a phone.
    expect(screen.queryByText("Tools")).toBeNull();
    expect(screen.queryByRole("link", { name: /tools/i })).toBeNull();
  });

  test("every row is a public /settings path", () => {
    renderInProviders(<MobileSettingsPage />);

    const hrefs = screen
      .getAllByRole("link")
      .map((link) => link.getAttribute("href") ?? "");

    expect(hrefs).toHaveLength(SECTION_ROWS.length);
    for (const href of hrefs) {
      expect(href.startsWith("/settings/")).toBe(true);
      expect(href).not.toContain("/m/");
    }
  });

  test("only the two read-only rows carry the badge", () => {
    renderInProviders(<MobileSettingsPage />);

    expect(
      within(rowLink("mobile-settings-row-models")).getByText("Read only"),
    ).toBeTruthy();
    expect(
      within(rowLink("mobile-settings-row-skills")).getByText("Read only"),
    ).toBeTruthy();
    // The badge is the difference between S5/S8 and every other row; a stray
    // third badge would mean the list lost that distinction.
    expect(screen.getAllByText("Read only")).toHaveLength(2);
    expect(
      within(rowLink("mobile-settings-row-channels")).queryByText("Read only"),
    ).toBeNull();
  });

  test("the account row shows the session's own address and about shows the version", () => {
    mockAuth.current = {
      user: { email: "someone@sz-jlc.com" },
      logout: rs.fn(),
    };
    renderInProviders(<MobileSettingsPage />);

    expect(
      within(rowLink("mobile-settings-row-account")).getByText(
        "someone@sz-jlc.com",
      ),
    ).toBeTruthy();
    // The value is the build's own version, so it can only be shape-checked.
    expect(
      within(rowLink("mobile-settings-row-about")).getByText(
        /^v\d+\.\d+\.\d+$/,
      ),
    ).toBeTruthy();
  });
});

describe("Mobile appearance section (S2)", () => {
  test("offers the desktop's three themes and calls setTheme", async () => {
    const user = userEvent.setup();
    renderInProviders(<MobileAppearanceSettingsPage />);
    expectPublicBackLink();

    expect(screen.getAllByRole("radio").length).toBeGreaterThanOrEqual(3);
    await user.click(screen.getByRole("radio", { name: /dark/i }));

    expect(mockTheme.setTheme).toHaveBeenCalledWith("dark");
  });

  test("switching the language re-renders the screen in it", async () => {
    const user = userEvent.setup();
    renderInProviders(<MobileAppearanceSettingsPage />);

    expect(screen.getByRole("heading", { name: "Appearance" })).toBeTruthy();
    await user.click(screen.getByRole("radio", { name: "中文" }));

    // The same `changeLocale` the desktop calls: the cookie and the live
    // context both move, so the header follows without a reload.
    expect(screen.getByRole("heading", { name: "外观" })).toBeTruthy();
  });
});

describe("Mobile notification section (S3)", () => {
  test("the switch follows the browser permission and writes the stored flag", async () => {
    const user = userEvent.setup();
    rs.stubGlobal("Notification", {
      permission: "granted",
      requestPermission: rs.fn(async () => "granted"),
    });
    renderInProviders(<MobileNotificationSettingsPage />);
    expectPublicBackLink();

    const toggle = await screen.findByRole("switch");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(toggle.hasAttribute("disabled")).toBe(false);
    // Permission granted and the flag on: the desktop's test button appears.
    expect(
      screen.getByRole("button", { name: /send test notification/i }),
    ).toBeTruthy();

    await user.click(toggle);

    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });
});

describe("Mobile models section (S5, read-only)", () => {
  test("lists the configured models, with no way to change them", async () => {
    stubBackend(ALL_ROUTES);
    renderInProviders(<MobileModelsSettingsPage />);
    expectPublicBackLink();

    expect(await screen.findAllByTestId("mobile-model-row")).toHaveLength(2);
    expect(screen.getByText("GPT-4o")).toBeTruthy();
    // The entry's model id, always present.
    expect(screen.getByText("gpt-4o")).toBeTruthy();
    // A model without a display name falls back to its key — which is then also
    // its id, so the row carries the same word twice on purpose.
    expect(screen.getAllByText("deepseek-chat").length).toBeGreaterThanOrEqual(
      1,
    );
    // The read-only notice, plus the badge the header repeats.
    expect(
      screen.getByText(/add, edit and remove on the desktop/i),
    ).toBeTruthy();
    expect(screen.getAllByText("Read only").length).toBeGreaterThanOrEqual(1);
    // No write control has been ported.
    expect(screen.queryByRole("button", { name: /add model/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^delete$/i })).toBeNull();
    // Models are administrator configuration: a non-admin reads the list
    // through the admin endpoint, so its write verbs must never be called.
    expect(fetchCalls.every((call) => !call.init?.method)).toBe(true);
  });

  test("a non-admin reads the public model list, not a permission wall", async () => {
    stubBackend([
      // Order matters: the stub takes the first route whose fragment matches,
      // and `/api/models` is a prefix of `/api/models/config`.
      {
        match: "/api/models/config",
        body: { detail: "Admin privileges required" },
        status: 403,
      },
      {
        match: "/api/models",
        body: {
          models: [
            {
              id: "gpt-4o",
              name: "gpt-4o",
              model: "gpt-4o",
              display_name: "GPT-4o",
            },
          ],
          vision_models: [],
          token_usage: { enabled: false },
        },
      },
    ]);
    renderInProviders(<MobileModelsSettingsPage />);

    // 403 on the managed region is expected for a normal account: the screen
    // falls back to the models that account can actually chat with, and stays
    // visibly read-only.
    expect(await screen.findAllByTestId("mobile-model-row")).toHaveLength(1);
    expect(screen.getByText("GPT-4o")).toBeTruthy();
    expect(screen.getAllByText("Read only").length).toBeGreaterThanOrEqual(1);
  });

  test("an empty managed list says so rather than looking broken", async () => {
    stubBackend([{ match: "/api/models/config", body: { models: [] } }]);
    renderInProviders(<MobileModelsSettingsPage />);

    expect(await screen.findByText("No model configs yet.")).toBeTruthy();
    expect(screen.queryAllByTestId("mobile-model-row")).toHaveLength(0);
  });
});

describe("Mobile skills section (S8, read-only)", () => {
  test("lists skills with their enabled state and no toggle", async () => {
    stubBackend(ALL_ROUTES);
    renderInProviders(<MobileSkillsSettingsPage />);
    expectPublicBackLink();

    expect(await screen.findAllByTestId("mobile-skill-row")).toHaveLength(2);
    const enabled = screen.getByRole("switch", { name: "data-analysis" });
    const disabled = screen.getByRole("switch", { name: "frontend-design" });
    expect(enabled.getAttribute("aria-checked")).toBe("true");
    expect(disabled.getAttribute("aria-checked")).toBe("false");
    // Read-only means the state is visible and immovable.
    expect(enabled.hasAttribute("disabled")).toBe(true);
    expect(disabled.hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: /create skill/i })).toBeNull();
  });

  test("the public/custom filter keeps the desktop's default", async () => {
    const user = userEvent.setup();
    stubBackend(ALL_ROUTES);
    renderInProviders(<MobileSkillsSettingsPage />);

    await screen.findAllByTestId("mobile-skill-row");
    expect(screen.queryByText("team-runbook")).toBeNull();

    await user.click(screen.getByTestId("mobile-skills-filter-custom"));

    expect(screen.getAllByTestId("mobile-skill-row")).toHaveLength(1);
    expect(screen.getByText("team-runbook")).toBeTruthy();
  });
});

describe("Mobile channels section (S6, degraded)", () => {
  test("shows connection status and offers no binding flow", async () => {
    stubBackend(ALL_ROUTES);
    renderInProviders(<MobileChannelsSettingsPage />);
    expectPublicBackLink();

    expect(await screen.findAllByTestId("mobile-channel-row")).toHaveLength(2);
    expect(screen.getByText("Telegram")).toBeTruthy();
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText(/Connected as deer-bot/)).toBeTruthy();
    expect(screen.getByText("Not connected")).toBeTruthy();
    // Binding happens on the desktop, and the section says so.
    expect(
      screen.getByText(/add, edit and remove on the desktop/i),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /connect/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /disconnect/i })).toBeNull();
  });

  test("a server without channel connections says so", async () => {
    stubBackend([
      {
        match: "/api/channels/providers",
        body: { enabled: false, providers: [] },
      },
      { match: "/api/channels/connections", body: { connections: [] } },
    ]);
    renderInProviders(<MobileChannelsSettingsPage />);

    expect(
      await screen.findByText(/Channel connections are not enabled/i),
    ).toBeTruthy();
  });
});

describe("Mobile about section (S9)", () => {
  test("shows the build's version", () => {
    renderInProviders(<MobileAboutSettingsPage />);
    expectPublicBackLink();

    expect(screen.getByText("Version")).toBeTruthy();
    expect(screen.getByTestId("mobile-about-version").textContent).toMatch(
      /^v\d+\.\d+\.\d+$/,
    );
  });
});
