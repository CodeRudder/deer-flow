import { afterEach, describe, expect, test, rs } from "@rstest/core";

import type { User } from "@/core/auth/types";

const user: User = {
  id: "user-1",
  email: "user@example.com",
  system_role: "user",
  needs_setup: false,
  oauth_provider: null,
};

async function loadHomeGatewayUnavailable(currentUser: User | null) {
  const refresh = rs.fn();
  const useEffect = rs.fn((effect: () => void) => {
    effect();
  });

  rs.resetModules();
  rs.doMock("next/navigation", () => ({
    useRouter: () => ({ refresh }),
  }));
  rs.doMock("react", () => ({ useEffect }));
  rs.doMock("@/core/auth/AuthProvider", () => ({
    useAuth: () => ({ user: currentUser }),
  }));
  rs.doMock("@/components/ui/button", () => ({
    Button: (props: Record<string, unknown>) => ({
      props,
      type: "button",
    }),
  }));
  rs.doMock("lucide-react", () => ({
    RefreshCwIcon: () => null,
  }));

  const { HomeGatewayUnavailable } =
    await import("@/app/home-gateway-unavailable");

  return {
    HomeGatewayUnavailable,
    refresh,
  };
}

afterEach(() => {
  rs.doUnmock("next/navigation");
  rs.doUnmock("react");
  rs.doUnmock("@/core/auth/AuthProvider");
  rs.doUnmock("@/components/ui/button");
  rs.doUnmock("lucide-react");
  rs.resetModules();
});

describe("HomeGatewayUnavailable", () => {
  test("does not refresh while no user is available", async () => {
    const { HomeGatewayUnavailable, refresh } =
      await loadHomeGatewayUnavailable(null);

    HomeGatewayUnavailable();

    expect(refresh).not.toHaveBeenCalled();
  });

  test("refreshes when the gateway probe restores a user", async () => {
    const { HomeGatewayUnavailable, refresh } =
      await loadHomeGatewayUnavailable(user);

    HomeGatewayUnavailable();

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  test("refreshes when the retry button is clicked", async () => {
    const { HomeGatewayUnavailable, refresh } =
      await loadHomeGatewayUnavailable(null);

    const element = HomeGatewayUnavailable();
    const retryButton = Array.isArray(element.props.children)
      ? element.props.children[1]
      : undefined;

    retryButton.props.onClick();

    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
