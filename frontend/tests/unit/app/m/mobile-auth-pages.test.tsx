import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement } from "react";

import MobileAuthCallbackPage from "@/app/m/(auth)/auth/callback/page";
import MobileLoginPage from "@/app/m/(auth)/login/page";
import MobileSetupPage from "@/app/m/(auth)/setup/page";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile auth screens (T3).
 *
 * These live here rather than in `tests/e2e/mobile-auth.spec.ts` because the
 * E2E harness cannot reach the pages: `playwright.config.ts` runs the app with
 * `DEER_FLOW_AUTH_DISABLED=1`, and `getServerSideUser()` returns a synthetic
 * `authenticated` user for that flag *without making a request*, so the auth
 * layout redirects to `/workspace` before anything renders. `page.route()`
 * cannot influence it — there is no HTTP call to intercept. The E2E spec keeps
 * the part that is observable there (middleware routing and the redirect),
 * and the rendered-page assertions live in this file.
 *
 * Strings come from the real `en-US` translations rather than a hand-rolled
 * stub, so a missing key fails here rather than silently rendering `undefined`.
 */

const mockRouter = rs.hoisted(() => ({
  push: rs.fn(),
  replace: rs.fn(),
  prefetch: rs.fn(),
  back: rs.fn(),
}));

const mockSearchParams = rs.hoisted(() => ({ current: new URLSearchParams() }));

const mockAuth = rs.hoisted(() => ({
  current: { isAuthenticated: false, user: null } as {
    isAuthenticated: boolean;
    user: { needs_setup?: boolean } | null;
  },
}));

rs.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams.current,
  usePathname: () => "/login",
}));

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => mockAuth.current,
  AuthProvider: ({ children }: { children: ReactElement }) => children,
}));

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  mockAuth.current = { isAuthenticated: false, user: null };
  mockSearchParams.current = new URLSearchParams();
  fetchCalls.length = 0;
  ssoProviders = [];
  needsSetup = false;
  authHandler = null;
});

/** Minimal `Response` stand-in — the pages only read ok/status/json. */
function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

/** `fetch` takes a string, a `URL`, or a `Request`; the pages pass a string. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

type SsoProvider = { id: string; display_name: string; type: string };

let ssoProviders: SsoProvider[] = [];
let needsSetup = false;
/** Answers anything the two auth endpoints above do not handle. */
let authHandler: ((url: string) => Response) | null = null;

const fetchCalls: { url: string; init?: RequestInit }[] = [];

/**
 * Installs the backend stub. `/auth/providers` and `/auth/setup-status` are always
 * answered from the mutable state above; everything else goes to `authHandler`.
 */
function stubBackend(handler?: (url: string) => Response) {
  authHandler = handler ?? null;
  rs.stubGlobal(
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      fetchCalls.push({ url, init });

      if (url.includes("/api/v1/auth/providers")) {
        return jsonResponse({ providers: ssoProviders });
      }
      if (url.includes("/api/v1/auth/setup-status")) {
        return jsonResponse({ needs_setup: needsSetup });
      }
      return authHandler ? authHandler(url) : jsonResponse({}, 200);
    },
  );
}

function callsTo(fragment: string) {
  return fetchCalls.filter((call) => call.url.includes(fragment));
}

function renderInProviders(ui: ReactElement) {
  return render(<I18nProvider initialLocale="en-US">{ui}</I18nProvider>);
}

/**
 * The two rules iOS punishes: below 16px the page zooms on focus, and a touch
 * target under 44px is a mis-tap. jsdom does not apply Tailwind (`getComputedStyle`
 * reports "medium"), so the class contract is asserted directly — it is the
 * exact string that produces the computed value in a real browser.
 */
function expectMobileFieldContract(el: HTMLElement) {
  const className = el.className;
  expect(className).toContain("text-base");
  expect(className).toContain("h-12");
}

describe("MobileLoginPage", () => {
  test("email input is type=email and both fields carry the mobile field contract", async () => {
    stubBackend();
    renderInProviders(<MobileLoginPage />);

    const email = await screen.findByLabelText("Email");
    expect(email.getAttribute("type")).toBe("email");
    expectMobileFieldContract(email);

    const password = screen.getByLabelText("Password");
    expect(password.getAttribute("type")).toBe("password");
    expectMobileFieldContract(password);
  });

  test("signs in against the local login endpoint with urlencoded credentials", async () => {
    stubBackend(() => jsonResponse({ id: "user-1" }));
    renderInProviders(<MobileLoginPage />);

    await userEvent.type(await screen.findByLabelText("Email"), "admin@sz-jlc.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret123");
    await userEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/workspace");
    });

    const [call] = callsTo("/api/v1/auth/login/local");
    expect(call!.url).toBe("/api/v1/auth/login/local");
    // Desktop posts `username`, not `email`, on this endpoint.
    expect(call!.init?.body).toBe(
      "username=admin%40sz-jlc.com&password=secret123",
    );
    expect(call!.init?.method).toBe("POST");
  });

  test("redirects to the public workspace path, never the /m/ tree", async () => {
    stubBackend();
    mockAuth.current = { isAuthenticated: true, user: null };
    renderInProviders(<MobileLoginPage />);

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/workspace");
    });
    for (const [target] of mockRouter.push.mock.calls) {
      expect(String(target).startsWith("/m/")).toBe(false);
    }
  });

  test("keeps an on-site ?next= target", async () => {
    stubBackend();
    mockAuth.current = { isAuthenticated: true, user: null };
    mockSearchParams.current = new URLSearchParams({
      next: "/workspace/chats/abc",
    });
    renderInProviders(<MobileLoginPage />);

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/workspace/chats/abc");
    });
  });

  test("ignores an off-site ?next= and falls back to the workspace", async () => {
    stubBackend();
    mockAuth.current = { isAuthenticated: true, user: null };
    mockSearchParams.current = new URLSearchParams({
      next: "https://evil.example/phish",
    });
    renderInProviders(<MobileLoginPage />);

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/workspace");
    });
  });

  test("renders the pending-approval card on a 202 registration", async () => {
    stubBackend(() =>
      jsonResponse({ status: "pending", message: "Awaiting" }, 202),
    );
    renderInProviders(<MobileLoginPage />);

    // The register toggle is only offered once setup-status says the system is ready.
    await userEvent.click(
      await screen.findByRole("button", { name: /sign up with your company email/i }),
    );
    await userEvent.type(screen.getByLabelText("Email"), "new@sz-jlc.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret1234");
    await userEvent.click(screen.getByRole("button", { name: "Create Account" }));

    expect(await screen.findByText("Application submitted")).toBeTruthy();
    // No session was created, so no navigation happened.
    expect(mockRouter.push).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  test("maps a 401 to the localized message and shows the SSO hint only with providers", async () => {
    // A configured provider is what makes the SSO hint eligible at all.
    ssoProviders = [{ id: "okta", display_name: "Okta", type: "oidc" }];
    stubBackend(() =>
      jsonResponse(
        {
          detail: {
            code: "invalid_credentials",
            message: "Incorrect email or password",
          },
        },
        401,
      ),
    );

    renderInProviders(<MobileLoginPage />);

    await userEvent.type(await screen.findByLabelText("Email"), "a@b.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign In" }));

    expect(await screen.findByText("Incorrect email or password")).toBeTruthy();
    expect(await screen.findByText(/single sign-on/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /continue with okta/i })).toBeTruthy();
    expect(mockRouter.push).not.toHaveBeenCalled();
  });
});

describe("MobileSetupPage", () => {
  test("renders the admin bootstrap form with the mobile field contract", async () => {
    needsSetup = true;
    stubBackend(() => jsonResponse({}));

    renderInProviders(<MobileSetupPage />);

    const email = await screen.findByLabelText("Email");
    expect(email.getAttribute("type")).toBe("email");
    expectMobileFieldContract(email);
    expect(
      screen.getByRole("button", { name: /create admin account/i }),
    ).toBeTruthy();
  });

  test("rejects mismatched passwords before issuing any request", async () => {
    needsSetup = true;
    stubBackend(() => jsonResponse({}));

    renderInProviders(<MobileSetupPage />);

    await userEvent.type(await screen.findByLabelText("Email"), "admin@sz-jlc.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret1234");
    await userEvent.type(screen.getByLabelText("Confirm Password"), "different123");
    await userEvent.click(
      screen.getByRole("button", { name: /create admin account/i }),
    );

    expect(await screen.findByText("Passwords do not match")).toBeTruthy();
    // The mismatch is caught client-side — the init endpoint is never called.
    expect(callsTo("/auth/initialize")).toHaveLength(0);
  });
});

describe("MobileAuthCallbackPage", () => {
  test("continues to the workspace when the session probe succeeds", async () => {
    stubBackend(() => jsonResponse({ id: "user-1" }));
    renderInProviders(<MobileAuthCallbackPage />);

    await waitFor(
      () => {
        expect(mockRouter.replace).toHaveBeenCalledWith("/workspace");
      },
      { timeout: 3000 },
    );
  });

  test("returns to login with the sso_failed code when the probe fails", async () => {
    stubBackend(() => jsonResponse({ detail: "nope" }, 401));
    renderInProviders(<MobileAuthCallbackPage />);

    await waitFor(
      () => {
        expect(mockRouter.replace).toHaveBeenCalledWith(
          "/login?error=sso_failed",
        );
      },
      { timeout: 3000 },
    );
  });
});
