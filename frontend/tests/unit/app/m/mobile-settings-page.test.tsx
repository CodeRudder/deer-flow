import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement } from "react";

import MobileSettingsPage from "@/app/m/(app)/settings/page";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile settings screen (S1, account section).
 *
 * The behaviour under test is the desktop `AccountSettingsPage`'s, which has no
 * test of its own; this file pins the parts a mobile port can silently get
 * wrong — that the two client-side checks run *before* the request, that the
 * request body and CSRF header match the desktop's, that an SSO account gets
 * the notice instead of a password form it cannot use, and that sign-out is
 * wired to `logout`.
 *
 * Strings come from the real `en-US` translations rather than a stub, so a
 * missing key fails here instead of rendering `undefined`.
 */

const mockAuth = rs.hoisted(() => ({
  current: {
    user: null as { email?: string; system_role?: string; oauth_provider?: string } | null,
    logout: rs.fn(),
  },
}));

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => mockAuth.current,
}));

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  mockAuth.current = { user: null, logout: rs.fn() };
});

/** Minimal `Response` stand-in — the page only reads ok/status/json. */
function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const fetchCalls: { url: string; init?: RequestInit }[] = [];

function stubBackend(handler?: (url: string) => Response) {
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
      return handler ? handler(url) : jsonResponse({}, 200);
    },
  );
}

function callsTo(fragment: string) {
  return fetchCalls.filter((call) => call.url.includes(fragment));
}

function renderInProviders(ui: ReactElement) {
  return render(<I18nProvider initialLocale="en-US">{ui}</I18nProvider>);
}

/** Fills the three password fields by their accessible label. */
async function fillPasswords(
  user: ReturnType<typeof userEvent.setup>,
  current: string,
  next: string,
  confirm = next,
) {
  await user.type(screen.getByLabelText("Current password"), current);
  await user.type(screen.getByLabelText("New password"), next);
  await user.type(screen.getByLabelText("Confirm new password"), confirm);
}

describe("Mobile settings page", () => {
  test("shows the signed-in account", () => {
    mockAuth.current = {
      user: { email: "someone@sz-jlc.com", system_role: "admin" },
      logout: rs.fn(),
    };
    renderInProviders(<MobileSettingsPage />);

    expect(screen.getByText("someone@sz-jlc.com")).toBeTruthy();
    expect(screen.getByText("admin")).toBeTruthy();
  });

  test("sign-out calls logout", async () => {
    const user = userEvent.setup();
    const logout = rs.fn();
    mockAuth.current = { user: { email: "a@sz-jlc.com" }, logout };
    renderInProviders(<MobileSettingsPage />);

    await user.click(screen.getByRole("button", { name: /sign out/i }));

    expect(logout).toHaveBeenCalledTimes(1);
  });

  test("a mismatched confirmation is rejected before any request", async () => {
    const user = userEvent.setup();
    stubBackend();
    renderInProviders(<MobileSettingsPage />);

    await fillPasswords(user, "old-password", "new-password-1", "different-1");
    await user.click(screen.getByRole("button", { name: /update password/i }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "New passwords do not match",
    );
    expect(callsTo("/change-password")).toHaveLength(0);
  });

  test("a short password is rejected before any request", async () => {
    const user = userEvent.setup();
    stubBackend();
    renderInProviders(<MobileSettingsPage />);

    // `minLength={8}` would stop a real submit; the check under test is the
    // handler's own, so the form is submitted directly.
    await fillPasswords(user, "old-password", "short", "short");
    screen.getByRole("button", { name: /update password/i }).click();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "at least 8 characters",
    );
    expect(callsTo("/change-password")).toHaveLength(0);
  });

  test("a successful change posts the desktop's body, reports success and clears the fields", async () => {
    const user = userEvent.setup();
    stubBackend(() => jsonResponse({ ok: true }));
    renderInProviders(<MobileSettingsPage />);

    await fillPasswords(user, "old-password", "new-password-1");
    await user.click(screen.getByRole("button", { name: /update password/i }));

    expect((await screen.findByRole("status")).textContent).toContain(
      "Password changed",
    );

    const [call] = callsTo("/change-password");
    expect(call?.init?.method).toBe("POST");
    expect(call?.init?.body).toBe(
      JSON.stringify({
        current_password: "old-password",
        new_password: "new-password-1",
      }),
    );
    // Same endpoint and CSRF header the desktop sends.
    expect(call?.url).toBe("/api/v1/auth/change-password");
    expect(call?.init?.headers?.["Content-Type"]).toBe("application/json");

    // Typed through the query's own generic rather than an `as` cast: the cast
    // is required by TS (`getByLabelText` is `HTMLElement`) but rejected by
    // `no-unnecessary-type-assertion`.
    const valueOf = (label: string) =>
      screen.getByLabelText<HTMLInputElement>(label).value;
    expect(valueOf("Current password")).toBe("");
    expect(valueOf("New password")).toBe("");
    expect(valueOf("Confirm new password")).toBe("");
  });

  test("a backend rejection surfaces its own message", async () => {
    const user = userEvent.setup();
    stubBackend(() =>
      jsonResponse({ detail: { code: "invalid_credentials", message: "Current password is incorrect" } }, 400),
    );
    renderInProviders(<MobileSettingsPage />);

    await fillPasswords(user, "wrong-password", "new-password-1");
    await user.click(screen.getByRole("button", { name: /update password/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain(
        "Current password is incorrect",
      );
    });
  });

  test("an SSO account gets the notice instead of a password form", () => {
    mockAuth.current = {
      user: { email: "a@sz-jlc.com", oauth_provider: "company_gateway" },
      logout: rs.fn(),
    };
    renderInProviders(<MobileSettingsPage />);

    expect(screen.queryByLabelText("Current password")).toBeNull();
    expect(screen.queryByRole("button", { name: /update password/i })).toBeNull();
    // The provider name is interpolated into the notice. Matched on the
    // notice's own wording rather than on the name: it also appears in the
    // profile row above, so a bare name match would be ambiguous.
    expect(
      screen.getByText(/cannot manage or change its password/).textContent,
    ).toContain("company_gateway");
    // Sign-out still applies.
    expect(screen.getByRole("button", { name: /sign out/i })).toBeTruthy();
  });
});
