import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  AgentCreateOption,
  isValidAgentName,
} from "@/components/workspace/agents/agent-create-sheet";

// The module pulls in the agents API (fetcher/config) and query hooks at
// import time; stub the network layer so the import never reaches for a
// real backend. Rendering below only touches the pure exports.
rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

afterEach(cleanup);

describe("isValidAgentName", () => {
  it.each(["code-reviewer", "a", "Agent-2", "x".repeat(64)])(
    "accepts %s",
    (name) => {
      expect(isValidAgentName(name)).toBe(true);
    },
  );

  it.each(["has space", "中文", "dot.name", "under_score", "", "trailing "])(
    "rejects %j",
    (name) => {
      expect(isValidAgentName(name)).toBe(false);
    },
  );
});

describe("AgentCreateOption", () => {
  it("renders title and description and forwards clicks", async () => {
    const user = userEvent.setup();
    let clicked = false;
    render(
      <AgentCreateOption
        icon={<span data-testid="icon" />}
        title="手动创建"
        description="填写名称、描述、模型与 SOUL.md"
        onClick={() => {
          clicked = true;
        }}
      />,
    );

    expect(screen.getByText("手动创建")).toBeTruthy();
    expect(screen.getByText("填写名称、描述、模型与 SOUL.md")).toBeTruthy();
    expect(document.querySelector("[data-testid='icon']")).toBeTruthy();

    await user.click(screen.getByRole("button"));
    expect(clicked).toBe(true);
  });
});
