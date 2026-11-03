import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { ProcessStepRow } from "@/components/workspace/messages/process-step-row";
import type { TimelineStep } from "@/core/messages/timeline";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "thinking" },
      subtasks: { failed: "failed" },
      processTimeline: {
        narrationLabel: "Note",
        viewMore: (count: number) => `View more (${count} chars left)`,
        viewLess: "Collapse to preview",
        openArtifact: "Open artifact",
      },
      toolCalls: {
        searchForRelatedInfo: "search for related info",
        searchFor: (query: string) => `search for ${query}`,
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

const openArtifact = rs.fn();
rs.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    setOpen: rs.fn(),
    select: openArtifact,
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
  }),
}));

const reasoningStep: TimelineStep = {
  id: "r1",
  messageId: "m1",
  variant: "reasoning",
  reasoning: "First line of thinking\nsecond line\nthird line",
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

const artifactStep: TimelineStep = {
  id: "tc2",
  messageId: "m1",
  variant: "toolCall",
  name: "write_file",
  args: { path: "draft.md", content: "x" },
  resultText: '"ok"',
  status: "done",
  opensArtifact: true,
};

function longText(lines: number) {
  return Array.from({ length: lines }, (_, i) => `line-${i + 1}`).join("\n");
}

afterEach(() => cleanup());

describe("ProcessStepRow", () => {
  it("collapsed: summary line plus a muted preview block (reasoning = first 3 lines)", () => {
    const { getByTestId } = render(<ProcessStepRow step={reasoningStep} />);
    const row = getByTestId("process-step-row");
    expect(row.textContent).toContain("thinking");
    const preview = getByTestId("process-step-preview");
    expect(preview.textContent).toContain("First line of thinking");
    expect(preview.textContent).toContain("third line");
    // preview is capped at 3 lines for reasoning
    expect(preview.textContent).not.toContain("fourth");
    expect(preview.textContent?.split("\n")).toHaveLength(3);
  });

  it("collapsed: narration shows the label + first 2 lines; toolCall shows the call digest", () => {
    const narration: TimelineStep = {
      id: "n1",
      messageId: "m1",
      variant: "narration",
      content: "one\ntwo\nthree",
      status: "done",
    };
    const { getByTestId } = render(<ProcessStepRow step={narration} />);
    expect(getByTestId("process-step-row").textContent).toContain("Note");
    const preview = getByTestId("process-step-preview");
    expect(preview.textContent).toContain("one");
    expect(preview.textContent).not.toContain("three");

    const bash: TimelineStep = {
      id: "tc3",
      messageId: "m1",
      variant: "toolCall",
      name: "bash",
      args: { command: "echo hi\necho bye" },
      status: "done",
    };
    cleanup();
    const view = render(<ProcessStepRow step={bash} />);
    expect(view.getByTestId("process-step-preview").textContent).toContain(
      "echo hi",
    );
  });

  it("streamingTail: the collapsed preview shows the tail lines instead of the head", () => {
    const streaming: TimelineStep = {
      id: "r2",
      messageId: "m2",
      variant: "reasoning",
      reasoning: longText(10),
      status: "done",
    };
    const { getByTestId } = render(
      <ProcessStepRow step={streaming} streamingTail={true} />,
    );
    const preview = getByTestId("process-step-preview");
    expect(preview.textContent).toContain("line-10");
    expect(preview.textContent).toContain("line-8");
    expect(preview.textContent).not.toContain("line-1\n");
    expect(preview.textContent?.startsWith("line-1")).toBe(false);
  });

  it("row click toggles expand/collapse for every variant — including artifact rows", () => {
    const { getByTestId } = render(<ProcessStepRow step={artifactStep} />);
    expect(openArtifact).not.toHaveBeenCalled();
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(getByTestId("process-step-detail")).not.toBeNull();
    // expanded row click collapses again
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(
      document.querySelectorAll('[data-testid="process-step-detail"]'),
    ).toHaveLength(0);
    expect(openArtifact).not.toHaveBeenCalled();
  });

  it("reasoning rows toggle on row click", () => {
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={reasoningStep} />,
    );
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(getByTestId("process-step-detail")).not.toBeNull();
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(queryByTestId("process-step-detail")).toBeNull();
  });

  it("expanded artifact detail offers the open-artifact action instead of the row click", () => {
    const { getByTestId } = render(<ProcessStepRow step={artifactStep} />);
    fireEvent.click(getByTestId("process-step-toggle"));
    const open = getByTestId("process-step-open-artifact");
    expect(open.textContent).toContain("draft.md");
    fireEvent.click(open);
    expect(openArtifact).toHaveBeenCalledTimes(1);
    expect(openArtifact.mock.calls[0]?.[0]).toContain("write-file:draft.md");
  });

  it("expanded write_file detail renders the file content, not the raw JSON args", () => {
    const writeFile: TimelineStep = {
      id: "tc4",
      messageId: "m1",
      variant: "toolCall",
      name: "write_file",
      args: {
        path: "report.md",
        content: "# Report\n\nReal document body",
        description: "write the report",
      },
      resultText: '"ok"',
      status: "done",
      opensArtifact: true,
    };
    const { getByTestId } = render(<ProcessStepRow step={writeFile} />);
    fireEvent.click(getByTestId("process-step-toggle"));
    const detail = getByTestId("process-step-detail");
    expect(detail.textContent).toContain("Real document body");
    expect(detail.textContent).not.toContain('"path"');
    expect(detail.textContent).not.toContain("content");
  });

  it("expanded detail is clamped to 10 lines with a view-more toggle showing remaining chars", () => {
    const long: TimelineStep = {
      id: "r3",
      messageId: "m3",
      variant: "reasoning",
      reasoning: longText(14),
      status: "done",
    };
    const { getByTestId } = render(<ProcessStepRow step={long} />);
    fireEvent.click(getByTestId("process-step-toggle"));
    const detail = getByTestId("process-step-detail");
    // clamped to 10 of 14 lines
    expect(detail.textContent).toContain("line-10");
    expect(detail.textContent).not.toContain("line-11");
    const more = getByTestId("process-step-clamp-more");
    // remaining chars = "line-11\n…line-14" plus newlines
    expect(more.textContent).toContain("View more");
    expect(more.textContent).toMatch(/\(\d+ chars left\)/);
    fireEvent.click(more);
    expect(getByTestId("process-step-detail").textContent).toContain("line-14");
    // click again re-collapses the block
    fireEvent.click(getByTestId("process-step-clamp-less"));
    expect(getByTestId("process-step-detail").textContent).not.toContain(
      "line-14",
    );
  });

  it("blocks within 10 lines render without a clamp toggle", () => {
    const { getByTestId, queryByTestId } = render(
      <ProcessStepRow step={reasoningStep} />,
    );
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(getByTestId("process-step-detail").textContent).toContain(
      "third line",
    );
    expect(queryByTestId("process-step-clamp-more")).toBeNull();
  });

  it("clicks inside the expanded detail never collapse the row", () => {
    const { getByTestId } = render(<ProcessStepRow step={toolStep} />);
    fireEvent.click(getByTestId("process-step-toggle"));
    const detail = getByTestId("process-step-detail");
    fireEvent.click(detail);
    expect(getByTestId("process-step-detail")).not.toBeNull();
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
    fireEvent.click(getByTestId("process-step-toggle"));
    expect(queryByTestId("process-step-detail")).toBeNull();
  });

  it("shows the done status symbol for a settled tool call", () => {
    const { getByTestId } = render(<ProcessStepRow step={toolStep} />);
    expect(getByTestId("process-step-row").textContent).toContain("✓");
  });

  it("D2 — forceExpanded shows the reasoning detail without user interaction", () => {
    const { getByTestId } = render(
      <ProcessStepRow step={reasoningStep} forceExpanded={true} />,
    );
    expect(getByTestId("process-step-detail")).not.toBeNull();
  });
});
