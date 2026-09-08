import type { Element, Root, ElementContent } from "hast";
import { useMemo } from "react";
import { visit } from "unist-util-visit";
import type { BuildVisitor } from "unist-util-visit";

const CJK_TEXT_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;

const MIN_GALLERY_IMAGES = 2;
const IMAGE_GALLERY_CLASS = "md-image-gallery";

/**
 * Turn image-only paragraphs holding two or more images into horizontal
 * gallery containers (styled by `.md-image-gallery` in globals.css).
 *
 * Whitespace-only text (soft line breaks) and hard breaks between images are
 * tolerated; paragraphs mixing visible text or other elements keep their
 * normal rendering. The paragraph is renamed rather than nested, because a
 * div inside a p is invalid HTML and react-markdown would re-split it.
 */
export function rehypeGroupImagesIntoGallery() {
  return (tree: Root) => {
    visit(tree, "element", ((node: Element) => {
      if (node.tagName !== "p") {
        return;
      }
      let imageCount = 0;
      for (const child of node.children) {
        if (child.type === "element") {
          if (child.tagName !== "img" && child.tagName !== "br") {
            return;
          }
          imageCount += child.tagName === "img" ? 1 : 0;
          continue;
        }
        if (child.type === "text" && child.value.trim() !== "") {
          return;
        }
        if (child.type !== "text") {
          return;
        }
      }
      if (imageCount < MIN_GALLERY_IMAGES) {
        return;
      }
      node.tagName = "div";
      const existing = node.properties.className;
      const classes = Array.isArray(existing)
        ? existing
        : existing
          ? [String(existing)]
          : [];
      node.properties = {
        ...node.properties,
        className: [...classes, IMAGE_GALLERY_CLASS],
      };
    }) as BuildVisitor<Root, "element">);
  };
}

export function rehypeSplitWordsIntoSpans() {
  return (tree: Root) => {
    visit(tree, "element", ((node: Element) => {
      if (
        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "strong"].includes(
          node.tagName,
        ) &&
        node.children
      ) {
        const newChildren: Array<ElementContent> = [];
        node.children.forEach((child) => {
          if (child.type === "text") {
            if (CJK_TEXT_RE.test(child.value)) {
              newChildren.push(child);
              return;
            }
            const segmenter = new Intl.Segmenter("zh", { granularity: "word" });
            const segments = segmenter.segment(child.value);
            const words = Array.from(segments)
              .map((segment) => segment.segment)
              .filter(Boolean);
            words.forEach((word: string) => {
              newChildren.push({
                type: "element",
                tagName: "span",
                properties: {
                  className: "animate-fade-in",
                },
                children: [{ type: "text", value: word }],
              });
            });
          } else {
            newChildren.push(child);
          }
        });
        node.children = newChildren;
      }
    }) as BuildVisitor<Root, "element">);
  };
}

export function useRehypeSplitWordsIntoSpans(enabled = true) {
  const rehypePlugins = useMemo(
    () => (enabled ? [rehypeSplitWordsIntoSpans] : []),
    [enabled],
  );
  return rehypePlugins;
}
