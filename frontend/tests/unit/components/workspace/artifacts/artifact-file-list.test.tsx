import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { ArtifactFileList } from "@/components/workspace/artifacts/artifact-file-list";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: { common: { install: "install", download: "download" } },
  }),
}));

rs.mock("@/components/workspace/artifacts/context", () => ({
  useArtifacts: () => ({
    select: () => undefined,
    setOpen: () => undefined,
  }),
}));

afterEach(cleanup);

function renderMessageVariant(files: string[]) {
  return render(
    <ArtifactFileList files={files} threadId="t1" variant="message" />,
  );
}

describe("ArtifactFileList message variant", () => {
  it("renders multiple images as one gallery grid", () => {
    const { container } = renderMessageVariant(["a.png", "b.png"]);
    const gallery = container.querySelector(".md-image-gallery");
    expect(gallery).toBeTruthy();
    expect(gallery?.querySelectorAll("img").length).toBe(2);
  });

  it("keeps a single image full-size without the gallery grid", () => {
    const { container } = renderMessageVariant(["a.png"]);
    expect(container.querySelector(".md-image-gallery")).toBeNull();
    expect(container.querySelectorAll("img").length).toBe(1);
  });

  it("opens the lightbox at the clicked thumbnail with a counter", () => {
    const { container, unmount } = renderMessageVariant([
      "a.png",
      "b.png",
      "c.png",
    ]);
    const thumbs = container.querySelectorAll<HTMLImageElement>(
      ".md-image-gallery img",
    );
    expect(thumbs.length).toBe(3);
    fireEvent.click(thumbs[1]!.closest("button")!);
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    expect(document.body.textContent).toContain("2 / 3");
    const lightboxImg = document.querySelector<HTMLImageElement>(
      '[role="dialog"] img',
    );
    expect(lightboxImg?.getAttribute("src")).toContain("b.png");
    unmount();
  });

  it("navigates images with arrow keys and wraps around", () => {
    const { container, unmount } = renderMessageVariant([
      "a.png",
      "b.png",
      "c.png",
    ]);
    const thumbs = container.querySelectorAll<HTMLImageElement>(
      ".md-image-gallery img",
    );
    fireEvent.click(thumbs[2]!.closest("button")!);
    expect(document.body.textContent).toContain("3 / 3");

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(document.body.textContent).toContain("1 / 3");
    expect(
      document.querySelector<HTMLImageElement>('[role="dialog"] img')?.src,
    ).toContain("a.png");

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(document.body.textContent).toContain("3 / 3");
    unmount();
  });

  it("closes the lightbox with Escape or by clicking the backdrop", () => {
    const { container, unmount } = renderMessageVariant(["a.png", "b.png"]);
    const thumbs = container.querySelectorAll<HTMLImageElement>(
      ".md-image-gallery img",
    );
    fireEvent.click(thumbs[0]!.closest("button")!);
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    // The img itself must not close the lightbox.
    fireEvent.click(document.querySelector('[role="dialog"] img')!);
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector('[role="dialog"]')).toBeNull();

    // Re-open and close via backdrop click.
    fireEvent.click(thumbs[0]!.closest("button")!);
    fireEvent.click(document.querySelector('[role="dialog"]')!);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    unmount();
  });

  it("opens the lightbox for a single image without the counter", () => {
    const { container, unmount } = renderMessageVariant(["a.png"]);
    const img = container.querySelector("img");
    expect(img).toBeTruthy();
    fireEvent.click(img!.closest("button")!);
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    expect(document.body.textContent).not.toContain(" / ");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    unmount();
  });
});
