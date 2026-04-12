import { afterEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "@testing-library/react";
import { useState, useCallback } from "react";

import type { Subtask } from "@/core/tasks/types";

// Mock subtask context with controllable state
const mockTasks: Record<string, Subtask> = {};
let mockUpdateSubtask = vi.fn();

vi.mock("@/core/tasks/context", () => ({
  useSubtask: (id: string) => mockTasks[id],
  useUpdateSubtask: () => mockUpdateSubtask,
  useSubtaskContext: () => ({ setSelectedTaskId: vi.fn() }),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      subtasks: {
        in_progress: "In progress",
        completed: "Completed",
        failed: "Failed",
        executing: (n: number) => `Executing ${n} task(s)`,
      },
      uploads: { uploadingFiles: "Uploading..." },
    },
  }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

vi.mock("@/core/rehype", () => ({
  useRehypeSplitWordsIntoSpans: () => [],
}));

vi.mock("@/core/streamdown", () => ({
  streamdownPluginsWithWordAnimation: {
    remarkPlugins: [],
    rehypePlugins: [],
  },
}));

vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
}));

vi.mock("rehype-katex", () => ({ default: () => () => {} }));
vi.mock("katex/dist/katex.min.css", () => ({}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock the relative import used in subtask-card.tsx
vi.mock("./context", () => ({
  useThread: () => ({ thread: { submit: vi.fn() } }),
}));

vi.mock("./markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <span>{content}</span>
  ),
}));

// Import after mocks
import { SubtaskCard } from "./subtask-card";

describe("SubtaskCard", () => {
  afterEach(() => {
    vi.clearAllMocks();
    for (const key of Object.keys(mockTasks)) {
      delete mockTasks[key];
    }
  });

  it("renders without crashing when task not in context (uses defaults)", () => {
    // Task not in mockTasks — should use default values, not throw
    const { container } = render(
      <SubtaskCard
        taskId="unknown-task"
        threadId="thread-1"
        isLoading={false}
      />,
    );
    // Component renders with empty default description
    expect(container).toBeTruthy();
  });

  it("renders in_progress status", () => {
    mockTasks["task-1"] = {
      id: "task-1",
      status: "in_progress",
      subagent_type: "general-purpose",
      description: "Build feature X",
      prompt: "Build feature X",
    };

    render(
      <SubtaskCard
        taskId="task-1"
        threadId="thread-1"
        isLoading={false}
      />,
    );

    expect(screen.getByText("Build feature X")).toBeTruthy();
    expect(screen.getByText("In progress")).toBeTruthy();
  });

  it("renders failed status with visible status label", () => {
    mockTasks["task-1"] = {
      id: "task-1",
      status: "failed",
      subagent_type: "general-purpose",
      description: "Build feature X",
      prompt: "Build feature X",
      error: "Something went wrong",
    };

    render(
      <SubtaskCard
        taskId="task-1"
        threadId="thread-1"
        isLoading={false}
      />,
    );

    // Description and status label are visible even when collapsed
    expect(screen.getByText("Build feature X")).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
  });

  it("renders completed status with visible status label", () => {
    mockTasks["task-1"] = {
      id: "task-1",
      status: "completed",
      subagent_type: "general-purpose",
      description: "Build feature X",
      prompt: "Build feature X",
      result: "Feature X is done",
    };

    render(
      <SubtaskCard
        taskId="task-1"
        threadId="thread-1"
        isLoading={false}
      />,
    );

    expect(screen.getByText("Build feature X")).toBeTruthy();
    expect(screen.getByText("Completed")).toBeTruthy();
  });
});
