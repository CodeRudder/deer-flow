import { afterEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CommandCard } from "./command-card";
import type { BackgroundCommand } from "./types";

// Mock API module
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const runningCommand: BackgroundCommand = {
  command_id: "cmd_001",
  command: "npm run dev",
  description: "Start dev server",
  status: "running",
  pid: 12345,
  started_at: new Date().toISOString(),
  return_code: null,
};

const completedCommand: BackgroundCommand = {
  command_id: "cmd_002",
  command: "python -m pytest",
  description: "Run tests",
  status: "completed",
  pid: null,
  started_at: new Date().toISOString(),
  return_code: 0,
};

describe("CommandCard", () => {
  it("renders command description and status", () => {
    render(
      <CommandCard command={runningCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );

    expect(screen.getByText("Start dev server")).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByText(/npm run dev/)).toBeTruthy();
  });

  it("shows PID for running commands", () => {
    render(
      <CommandCard command={runningCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );
    expect(screen.getByText(/PID: 12345/)).toBeTruthy();
  });

  it("shows return code for completed commands", () => {
    render(
      <CommandCard command={completedCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );
    expect(screen.getByText(/Exit: 0/)).toBeTruthy();
  });

  it("shows stop button for running commands", () => {
    render(
      <CommandCard command={runningCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );
    // Stop button is present for running commands
    const stopButton = screen.getByRole("button", { name: "" });
    expect(stopButton).toBeTruthy();
  });

  it("does not show stop button for completed commands", () => {
    render(
      <CommandCard command={completedCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );
    // No stop button, only "Output" button
    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBe(1); // Only Output button
  });

  it("toggles output view on Output button click", async () => {
    const user = userEvent.setup();

    render(
      <CommandCard command={completedCommand} threadId="thread-1" onKilled={vi.fn()} />,
    );

    // Output not shown initially
    expect(screen.queryByText(/No output available/)).toBeNull();

    // Click Output button
    await user.click(screen.getByText("Output"));

    // Now output section should be visible (loading state)
    // The output view will try to fetch, but since we mock fetch we check for loading text
    expect(screen.getByText("Hide")).toBeTruthy();
  });
});
