import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("streamdown", () => ({
  Streamdown: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
}));

import { MessageResponse } from "./message";

describe("MessageResponse", () => {
  it("keeps markdown content constrained inside the message width", () => {
    render(
      <MessageResponse>
        {"https://example.com/" + "very-long-path-segment".repeat(20)}
      </MessageResponse>,
    );

    const response = screen.getByText(/https:\/\/example\.com/);
    expect(response.className).toContain("min-w-0");
    expect(response.className).toContain("max-w-full");
    expect(response.className).toContain("[overflow-wrap:anywhere]");
  });
});
