import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchCommands, fetchOutput, killCommand } from "./api";

// Mock getBackendBaseURL
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

afterEach(() => {
  mockFetch.mockReset();
});

// ---------------------------------------------------------------------------
// fetchCommands
// ---------------------------------------------------------------------------

describe("fetchCommands", () => {
  it("returns commands on success", async () => {
    const commands = [
      { command_id: "cmd_1", command: "echo hi", status: "running" },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ commands }),
    });

    const result = await fetchCommands("thread-1");
    expect(result).toEqual(commands);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/thread-1/commands",
    );
  });

  it("returns empty array on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false });
    const result = await fetchCommands("thread-1");
    expect(result).toEqual([]);
  });

  it("returns empty array when commands field is missing", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });
    const result = await fetchCommands("thread-1");
    expect(result).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// fetchOutput
// ---------------------------------------------------------------------------

describe("fetchOutput", () => {
  it("returns output with pagination", async () => {
    const output = {
      command_id: "cmd_1",
      status: "running",
      output: "hello",
      pagination: { total_lines: 5, start_line: 0, line_count: 5, has_more: false },
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(output),
    });

    const result = await fetchOutput("thread-1", "cmd_1", 0, 10);
    expect(result).toEqual(output);
  });

  it("returns null on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false });
    const result = await fetchOutput("thread-1", "cmd_1");
    expect(result).toBeNull();
  });

  it("passes start_line only when defined", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });
    await fetchOutput("thread-1", "cmd_1");
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).not.toContain("start_line");
  });

  it("passes start_line when provided", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });
    await fetchOutput("thread-1", "cmd_1", 10, 20);
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("start_line=10");
    expect(url).toContain("line_count=20");
  });
});

// ---------------------------------------------------------------------------
// killCommand
// ---------------------------------------------------------------------------

describe("killCommand", () => {
  it("returns kill result on success", async () => {
    const killResult = { killed: true, message: "killed", final_output: "out" };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(killResult),
    });

    const result = await killCommand("thread-1", "cmd_1");
    expect(result).toEqual(killResult);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/thread-1/commands/cmd_1/kill",
      { method: "POST" },
    );
  });

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false });
    await expect(killCommand("thread-1", "cmd_1")).rejects.toThrow(
      "Failed to kill command",
    );
  });
});
