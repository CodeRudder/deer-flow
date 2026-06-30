import { expect, test, rs } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

rs.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: string }) =>
    createElement("div", null, children),
}));

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";

test("ReasoningTrigger default message uses phrasing content", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: false, defaultOpen: false },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  expect(html).toContain("Thought for a few seconds");
  expect(html).not.toMatch(/<button\b[^>]*>[\s\S]*?<p\b/i);
});

test("ReasoningTrigger renders completed zero-second duration as completed", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: false, defaultOpen: false, duration: 0 },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  expect(html).toContain("Thought for 0 seconds");
  expect(html).not.toContain("Thinking...");
});

test("ReasoningTrigger clamps negative completed duration to zero", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: false, defaultOpen: false, duration: -3 },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  expect(html).toContain("Thought for 0 seconds");
  expect(html).not.toContain("Thought for -3 seconds");
});

test("ReasoningTrigger falls back for non-finite completed duration", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: false, defaultOpen: false, duration: Number.NaN },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  expect(html).toContain("Thought for a few seconds");
  expect(html).not.toContain("NaN");
});

test("ReasoningTrigger still renders Thinking while streaming", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: true, defaultOpen: false, duration: 0 },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  expect(html).toContain("Thinking...");
});
