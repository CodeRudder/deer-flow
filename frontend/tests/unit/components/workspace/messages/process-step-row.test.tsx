import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { ProcessStepRow } from "@/components/workspace/messages/process-step-row";
import type { TimelineStep } from "@/core/messages/timeline";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "thinking" },
      subtasks: { failed: "failed" },
      toolCalls: {
        searchForRelatedInfo: "search for related info",
        searchOnWebFor: (query: string) => `search the web for ${query}`,
        searchForRelatedImages: "search for related images",
        searchForRelatedImagesFor: (query: string) => `image search ${query}`,
        viewWebPage: "view web page",
        listFolder: "list folder",
        readFile: "read file",
        writeFile: "write file",
        executeCommand: "execute command",
        writeTodos: "write todos",
        useTool: (name: string) => `use ${name}`,
      },
    },
  }),
}));

rs.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    setOpen: rs.fn(),
    select: rs.fn(),
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
  }),
}));

const reasoningStep: TimelineStep = {
  id: "r1",
  messageId: "m1",
  variant: "reasoning",
  reasoning: "First line of thinking\nsecond line",
  status: "done",
};

const toolStep: TimelineStep = {
  id: "tc1",
  messageId: "m1",
  variant: "toolCall",
  name: "web_search",
  args: { query: "deer flow" },
  result: [{ url: "https://example.com", title: "Example" }],
  resultText: "[]",
  status: "done",
};

afterEach(() => cleanup());

describe("ProcessStepRow", () => {
  it("renders a single collapsed row with the tool summary label", () => {
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={toolStep} />,
    );
    const row = getByTestId("process-step-row");
    expect(row.getAttribute("aria-expanded")).toBe(null);
    expect(row.textContent).toContain("search the web for deer flow");
    expect(queryByTestId("process-step-detail")).toBe(null);
  });

  it("expands on click and collapses again", () => {
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={toolStep} />,
    );
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(getByTestId("process-step-detail")).not.toBeNull();
    expect(
      getByTestId("process-step-toggle").getAttribute("aria-expanded"),
    ).toBe("true");
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(queryByTestId("process-step-detail")).toBe(null);
  });

  it("shows the reasoning summary truncated to its first line and expands to full text", () => {
    const { getByTestId } = render(<ProcessStepRow step={reasoningStep} />);
    const row = getByTestId("process-step-row");
    expect(row.textContent).toContain("First line of thinking");
    expect(row.textContent).not.toContain("second line");
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(getByTestId("process-step-detail").textContent).toContain(
      "second line",
    );
  });

  it("marks the running tool call and spins while loading", () => {
    const running: TimelineStep = { ...toolStep, status: "running" };
    const { getByTestId } = render(
      <ProcessStepRow step={running} isLoading={true} />,
    );
    expect(
      getByTestId("process-step-row").getAttribute("data-step-status"),
    ).toBe("running");
    expect(document.querySelector(".animate-spin")).not.toBeNull();
  });

  it("D7 — a failed step opens with its error detail by default and can be collapsed", () => {
    const failed: TimelineStep = {
      ...toolStep,
      status: "failed",
      result: undefined,
      resultText: undefined,
    };
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={failed} failed={true} />,
    );
    expect(getByTestId("process-step-detail")).not.toBeNull();
    expect(getByTestId("process-step-row").textContent).toContain("failed");
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(queryByTestId("process-step-detail")).toBe(null);
  });

  it("renders the done status symbol for a settled tool call", () => {
    const { getByTestId } = render(<ProcessStepRow step={toolStep} />);
    expect(getByTestId("process-step-row").textContent).toContain("✓");
  });

  it("D2 — forceExpanded shows the reasoning detail without user interaction", () => {
    const { getByTestId } = render(
      <ProcessStepRow step={reasoningStep} forceExpanded={true} />,
    );
    expect(getByTestId("process-step-detail")).not.toBeNull();
  });

  it("renders a narration variant as a muted text row with full text on expand", () => {
    const narration: TimelineStep = {
      id: "n1",
      messageId: "m1",
      variant: "narration",
      content: "Now writing the file",
      status: "done",
    };
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={narration} />,
    );
    expect(
      getByTestId("process-step-row").getAttribute("data-step-variant"),
    ).toBe("narration");
    expect(getByTestId("process-step-row").textContent).toContain(
      "Now writing the file",
    );
    expect(queryByTestId("process-step-detail")).toBe(null);
  });
});
