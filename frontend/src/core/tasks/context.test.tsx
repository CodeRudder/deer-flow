import { afterEach, describe, expect, it, vi } from "vitest";

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useState, useCallback } from "react";

import type { Subtask } from "@/core/tasks/types";
import {
  SubtaskContext,
  SubtasksProvider,
  useSubtask,
  useSubtasks,
  useUpdateSubtask,
} from "./context";

// Mock dependencies
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// ------------------------------------------------------------------
// Test: useUpdateSubtask — status downgrade protection
// ------------------------------------------------------------------

function TestConsumer({ taskId }: { taskId: string }) {
  const task = useSubtask(taskId);
  const updateSubtask = useUpdateSubtask();
  return (
    <div>
      <span data-testid="status">{task?.status ?? "none"}</span>
      <button
        data-testid="set-completed"
        onClick={() =>
          updateSubtask({ id: taskId, status: "completed", result: "done" })
        }
      >
        complete
      </button>
      <button
        data-testid="set-in-progress"
        onClick={() =>
          updateSubtask({ id: taskId, status: "in_progress" })
        }
      >
        in_progress
      </button>
      <button
        data-testid="set-failed"
        onClick={() =>
          updateSubtask({ id: taskId, status: "failed", error: "oops" })
        }
      >
        fail
      </button>
    </div>
  );
}

function TasksListConsumer() {
  const tasks = useSubtasks();
  return (
    <span data-testid="count">{tasks.length}</span>
  );
}

describe("SubtaskContext", () => {
  it("prevents downgrade from completed to in_progress", async () => {
    const user = userEvent.setup();
    render(
      <SubtasksProvider>
        <TestConsumer taskId="t1" />
      </SubtasksProvider>,
    );

    // Initially no task
    expect(screen.getByTestId("status").textContent).toBe("none");

    // Set as completed
    await user.click(screen.getByTestId("set-completed"));
    expect(screen.getByTestId("status").textContent).toBe("completed");

    // Try to downgrade — should be ignored
    await user.click(screen.getByTestId("set-in-progress"));
    expect(screen.getByTestId("status").textContent).toBe("completed");
  });

  it("prevents downgrade from failed to in_progress", async () => {
    const user = userEvent.setup();
    render(
      <SubtasksProvider>
        <TestConsumer taskId="t1" />
      </SubtasksProvider>,
    );

    // Set as failed
    await user.click(screen.getByTestId("set-failed"));
    expect(screen.getByTestId("status").textContent).toBe("failed");

    // Try to downgrade — should be ignored
    await user.click(screen.getByTestId("set-in-progress"));
    expect(screen.getByTestId("status").textContent).toBe("failed");
  });

  it("allows in_progress -> completed transition", async () => {
    const user = userEvent.setup();
    render(
      <SubtasksProvider>
        <TestConsumer taskId="t1" />
      </SubtasksProvider>,
    );

    // Set as in_progress
    await user.click(screen.getByTestId("set-in-progress"));
    expect(screen.getByTestId("status").textContent).toBe("in_progress");

    // Upgrade to completed
    await user.click(screen.getByTestId("set-completed"));
    expect(screen.getByTestId("status").textContent).toBe("completed");
  });

  it("useSubtasks returns all tasks", async () => {
    const user = userEvent.setup();
    render(
      <SubtasksProvider>
        <TestConsumer taskId="t1" />
        <TestConsumer taskId="t2" />
        <TasksListConsumer />
      </SubtasksProvider>,
    );

    expect(screen.getByTestId("count").textContent).toBe("0");

    // Create two tasks
    await user.click(screen.getAllByTestId("set-in-progress")[0]!);
    await user.click(screen.getAllByTestId("set-in-progress")[1]!);
    expect(screen.getByTestId("count").textContent).toBe("2");
  });

  it("triggers re-render on status update", async () => {
    const user = userEvent.setup();
    render(
      <SubtasksProvider>
        <TestConsumer taskId="t1" />
      </SubtasksProvider>,
    );

    // Status update without latestMessage should still trigger re-render
    await user.click(screen.getByTestId("set-completed"));
    expect(screen.getByTestId("status").textContent).toBe("completed");

    await user.click(screen.getByTestId("set-failed"));
    // completed -> failed is allowed (re-evaluating result)
    expect(screen.getByTestId("status").textContent).toBe("failed");
  });
});
