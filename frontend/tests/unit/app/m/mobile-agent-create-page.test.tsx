import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MobileNewAgentChatPage from "@/app/m/(app)/(fullbleed)/workspace/agents/new/page";

/**
 * The chat-based creation flow (G4) on a phone.
 *
 * The screen is a port of the desktop's `/workspace/agents/new`, and the port
 * is only correct if the conversation it starts is the *same* one: the
 * bootstrap message must be `t.agents.nameStepBootstrapMessage` with the name
 * substituted, sent with `agent_name`, inside the `flash` + `is_bootstrap`
 * context the backend expects. That — plus "a taken/invalid name never starts a
 * conversation" — is what this file asserts.
 *
 * The chat surface below the name step (transcript, PromptInput) is stubbed:
 * what is under test is the page's own wiring, and the real composer has its
 * own suite. `onSubmit` is captured instead, so the send path can be driven
 * without a DOM textarea standing in for the registry component.
 */

const mocks = rs.hoisted(() => ({
  streamOptions: [] as Record<string, unknown>[],
  // Typed so the recorded call tuples are readable (`mock.calls[0]![3]`).
  sendMessage: rs.fn(
    async (
      _threadId: string,
      _message: { text: string; files: unknown[] },
      _extraContext?: Record<string, unknown>,
      _options?: { additionalKwargs?: Record<string, unknown> },
    ) => undefined,
  ),
  promptInputOnSubmit: [] as ((message: { text: string }) => void)[],
  checkAgentName: rs.fn(async () => ({
    available: true,
    name: "weekly-report",
  })),
  routerPush: rs.fn(),
  toastSuccess: rs.fn(),
  toastError: rs.fn(),
}));

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush, replace: rs.fn() }),
}));

rs.mock("sonner", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    locale: "en-US",
    t: {
      pages: { appName: "DeerFlow" },
      agents: {
        title: "Agents",
        createPageTitle: "Create an agent",
        createPageSubtitle: "Tell me what the agent should do",
        nameLabel: "Name",
        nameStepTitle: "Name your agent",
        nameStepHint: "Letters, numbers and hyphens",
        nameStepPlaceholder: "my-agent",
        nameStepContinue: "Continue",
        nameStepInvalidError: "Invalid name",
        nameStepAlreadyExistsError: "That name is taken",
        nameStepNetworkError: "Backend unreachable",
        nameStepCheckError: "Could not check the name",
        nameStepCheckErrorWithDetail: "Could not check the name: {detail}",
        nameStepApiDisabledError: "Agents API disabled",
        nameStepBootstrapMessage: "Help me build the agent {name}.",
        save: "Save",
        saving: "Saving",
        saveRequested: "Save requested",
        saveHint: "Say when you are happy and I will save it",
        saveCommandMessage: "Save this agent now",
        more: "More",
        agentCreated: "Agent created",
        agentCreatedPendingRefresh: "Created, refresh to see it",
        startChatting: "Start chatting",
        backToGallery: "Back to gallery",
      },
    },
  }),
}));

rs.mock("@/core/threads/hooks", () => ({
  useThreadStream: (options: Record<string, unknown>) => {
    mocks.streamOptions.push(options);
    return {
      thread: {
        messages: [],
        values: {},
        isLoading: false,
        error: undefined,
        stop: rs.fn(),
      },
      sendMessage: mocks.sendMessage,
    };
  },
}));

rs.mock("@/core/agents/api", () => ({
  checkAgentName: mocks.checkAgentName,
  getAgent: rs.fn(async () => {
    throw new Error("not created");
  }),
  AgentsApiDisabledError: class AgentsApiDisabledError extends Error {},
  AgentNameCheckError: class AgentNameCheckError extends Error {
    reason = "request_failed";
    detail: string | undefined = undefined;
  },
}));

rs.mock("@/components/workspace/messages", () => ({
  MessageList: () => null,
}));

rs.mock("@/components/workspace/artifacts", () => ({
  ArtifactsProvider: ({ children }: { children: React.ReactNode }) => children,
}));

rs.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInput: ({
    children,
    onSubmit,
  }: {
    children: React.ReactNode;
    onSubmit: (message: { text: string }) => void;
  }) => {
    mocks.promptInputOnSubmit.push(onSubmit);
    return <div data-testid="stub-prompt-input">{children}</div>;
  },
  PromptInputTextarea: () => <textarea data-testid="stub-textarea" />,
  PromptInputFooter: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  PromptInputSubmit: () => <button type="submit" />,
}));

beforeEach(() => {
  mocks.streamOptions = [];
  mocks.promptInputOnSubmit = [];
  mocks.checkAgentName.mockImplementation(async () => ({
    available: true,
    name: "weekly-report",
  }));
});

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
  window.localStorage.clear();
});

async function enterNameStep(name: string) {
  render(<MobileNewAgentChatPage />);
  await userEvent.type(screen.getByTestId("mobile-agent-create-name"), name);
  await userEvent.click(screen.getByTestId("mobile-agent-create-name-submit"));
}

describe("mobile chat-based agent creation", () => {
  test("starts the desktop's bootstrap conversation, not one of its own", async () => {
    await enterNameStep("weekly-report");

    await waitFor(() => {
      expect(mocks.sendMessage).toHaveBeenCalledTimes(1);
    });

    // The desktop's copy, with `{name}` substituted — the same locale key.
    expect(mocks.sendMessage.mock.calls[0]![0]).toEqual(expect.any(String));
    expect(mocks.sendMessage.mock.calls[0]![1]).toEqual({
      text: "Help me build the agent weekly-report.",
      files: [],
    });
    expect(mocks.sendMessage.mock.calls[0]![2]).toEqual({
      agent_name: "weekly-report",
    });
    expect(mocks.checkAgentName).toHaveBeenCalledWith("weekly-report");

    // The bootstrap run's context is the desktop's: a flash run flagged as the
    // creation conversation.
    expect(mocks.streamOptions.at(-1)!.context).toEqual({
      mode: "flash",
      is_bootstrap: true,
    });
    // The thread is created by the run, so the stream starts without one.
    expect(mocks.streamOptions.at(-1)!.threadId).toBeUndefined();
  });

  test("the name step shows no conversation at all", async () => {
    render(<MobileNewAgentChatPage />);

    expect(screen.getByTestId("mobile-agent-create-name")).toBeTruthy();
    expect(screen.queryByTestId("stub-prompt-input")).toBeNull();
  });

  test("an invalid name is refused before any request", async () => {
    await enterNameStep("has space");

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(screen.getByRole("alert").textContent).toContain("Invalid name");
    expect(mocks.checkAgentName).not.toHaveBeenCalled();
    expect(mocks.sendMessage).not.toHaveBeenCalled();
    expect(screen.queryByTestId("stub-prompt-input")).toBeNull();
  });

  test("a taken name stays on the name step", async () => {
    mocks.checkAgentName.mockImplementation(async () => ({
      available: false,
      name: "weekly-report",
    }));

    await enterNameStep("weekly-report");

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("taken");
    });
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });

  test("follow-up messages in the conversation carry agent_name", async () => {
    await enterNameStep("weekly-report");
    await waitFor(() => {
      expect(mocks.promptInputOnSubmit.length).toBeGreaterThan(0);
    });

    // The stub records one entry per render, so the latest is the live one.
    mocks.promptInputOnSubmit.at(-1)!({ text: "Make it write Markdown" });

    await waitFor(() => {
      expect(mocks.sendMessage).toHaveBeenCalledTimes(2);
    });
    expect(mocks.sendMessage.mock.calls[1]![1]).toEqual({
      text: "Make it write Markdown",
      files: [],
    });
    expect(mocks.sendMessage.mock.calls[1]![2]).toEqual({
      agent_name: "weekly-report",
    });
  });

  test("the save command asks for the write and hides itself from the transcript", async () => {
    await enterNameStep("weekly-report");
    await waitFor(() => {
      expect(screen.getByTestId("mobile-agent-create-more")).toBeTruthy();
    });

    await userEvent.click(screen.getByTestId("mobile-agent-create-more"));
    const save = await screen.findByTestId("mobile-agent-create-save");
    await userEvent.click(save);

    await waitFor(() => {
      expect(mocks.sendMessage).toHaveBeenCalledTimes(2);
    });
    expect(mocks.sendMessage.mock.calls[1]![1]).toEqual({
      text: "Save this agent now",
      files: [],
    });
    expect(mocks.sendMessage.mock.calls[1]![2]).toEqual({
      agent_name: "weekly-report",
    });
    expect(mocks.sendMessage.mock.calls[1]![3]).toEqual({
      additionalKwargs: { hide_from_ui: true },
    });
  });
});
