import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";

import { ClipboardSafeStreamdown } from "@/components/ai-elements/streamdown";
import {
  groupConsecutiveImageBlocks,
  streamdownPluginsWithoutRawHtml,
} from "@/core/streamdown";

afterEach(cleanup);

function renderMarkdown(markdown: string) {
  return render(
    <ClipboardSafeStreamdown {...streamdownPluginsWithoutRawHtml}>
      {groupConsecutiveImageBlocks(markdown)}
    </ClipboardSafeStreamdown>,
  );
}

describe("consecutive image gallery pipeline", () => {
  it("renders blank-line-separated images as one gallery strip", () => {
    const markdown = ["![a](u1)", "", "![b](u2)"].join("\n");
    const { container } = renderMarkdown(markdown);
    const gallery = container.querySelector(".md-image-gallery");
    expect(gallery).toBeTruthy();
    expect(gallery?.querySelectorAll("img").length).toBe(2);
  });

  it("renders a single image without a gallery wrapper", () => {
    const { container } = renderMarkdown(["![a](u1)", "", "text"].join("\n"));
    expect(container.querySelector(".md-image-gallery")).toBeNull();
    expect(container.querySelectorAll("img").length).toBe(1);
  });

  it("keeps image-and-text paragraphs out of the gallery", () => {
    const markdown = ["![a](u1)", "caption", "![b](u2)"].join("\n");
    const { container } = renderMarkdown(markdown);
    expect(container.querySelector(".md-image-gallery")).toBeNull();
  });

  it("renders same-paragraph multi-images as a gallery strip", () => {
    const markdown = ["![a](u1)", "![b](u2)"].join("\n");
    const { container } = renderMarkdown(markdown);
    const gallery = container.querySelector(".md-image-gallery");
    expect(gallery).toBeTruthy();
    expect(gallery?.querySelectorAll("img").length).toBe(2);
  });
});
