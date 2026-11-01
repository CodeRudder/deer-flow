import { afterEach, describe, expect, test, rs } from "@rstest/core";

import type { AuthResult } from "@/core/auth/types";

const authenticatedUser = {
  id: "user-1",
  email: "user@example.com",
  system_role: "user" as const,
  needs_setup: false,
  oauth_provider: null,
};

async function loadHomePage(result: AuthResult) {
  const redirect = rs.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  });
  const getServerSideUser = rs.fn(async () => result);

  rs.resetModules();
  rs.doMock("next/navigation", () => ({
    redirect,
  }));
  rs.doMock("@/core/auth/server", () => ({
    getServerSideUser,
  }));

  const { default: HomePage } = await import("@/app/page");

  return {
    getServerSideUser,
    HomePage,
    redirect,
  };
}

afterEach(() => {
  rs.doUnmock("next/navigation");
  rs.doUnmock("@/core/auth/server");
  rs.resetModules();
});

describe("HomePage", () => {
  test("redirects authenticated users to workspace", async () => {
    const { HomePage, redirect } = await loadHomePage({
      tag: "authenticated",
      user: authenticatedUser,
    });

    await expect(HomePage()).rejects.toThrow("NEXT_REDIRECT:/workspace");
    expect(redirect).toHaveBeenCalledWith("/workspace");
  });

  test("redirects unauthenticated users to login", async () => {
    const { HomePage, redirect } = await loadHomePage({
      tag: "unauthenticated",
    });

    await expect(HomePage()).rejects.toThrow("NEXT_REDIRECT:/login");
    expect(redirect).toHaveBeenCalledWith("/login");
  });

  test("redirects setup-required users to setup", async () => {
    const { HomePage, redirect } = await loadHomePage({
      tag: "needs_setup",
      user: { ...authenticatedUser, needs_setup: true },
    });

    await expect(HomePage()).rejects.toThrow("NEXT_REDIRECT:/setup");
    expect(redirect).toHaveBeenCalledWith("/setup");
  });

  test("redirects uninitialized systems to setup", async () => {
    const { HomePage, redirect } = await loadHomePage({
      tag: "system_setup_required",
    });

    await expect(HomePage()).rejects.toThrow("NEXT_REDIRECT:/setup");
    expect(redirect).toHaveBeenCalledWith("/setup");
  });

  test("renders the gateway-unavailable fallback without redirecting", async () => {
    const { HomePage, redirect } = await loadHomePage({
      tag: "gateway_unavailable",
    });

    await expect(HomePage()).resolves.toBeTruthy();
    expect(redirect).not.toHaveBeenCalled();
  });

  test("throws config errors", async () => {
    const { HomePage } = await loadHomePage({
      tag: "config_error",
      message: "missing gateway URL",
    });

    await expect(HomePage()).rejects.toThrow("missing gateway URL");
  });
});
