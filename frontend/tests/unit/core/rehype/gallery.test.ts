import { expect, test } from "@rstest/core";
import type { Element, Root } from "hast";

import { rehypeGroupImagesIntoGallery } from "@/core/rehype";

function img(url: string): Element {
  return {
    type: "element",
    tagName: "img",
    properties: { src: url, alt: "" },
    children: [],
  };
}

function p(...children: Root["children"]): Element {
  return {
    type: "element",
    tagName: "p",
    properties: {},
    children: children as Element["children"],
  };
}

function root(...children: Root["children"]): Root {
  return { type: "root", children };
}

function apply(...children: Root["children"]): Root {
  const tree = root(...children);
  rehypeGroupImagesIntoGallery()(tree);
  return tree;
}

test("renames a paragraph with two or more images into a gallery div", () => {
  const tree = apply(p(img("u1"), img("u2")));
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("div");
  expect(node.properties.className).toContain("md-image-gallery");
});

test("keeps a single-image paragraph as a normal paragraph", () => {
  const tree = apply(p(img("u1")));
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("p");
  expect(node.properties.className).toBeUndefined();
});

test("tolerates soft breaks and hard breaks between images", () => {
  const tree = apply(
    p(
      img("u1"),
      { type: "text", value: "\n" },
      img("u2"),
      {
        type: "element",
        tagName: "br",
        properties: {},
        children: [],
      },
      img("u3"),
    ),
  );
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("div");
  expect(node.properties.className).toContain("md-image-gallery");
});

test("keeps paragraphs that mix images with visible text", () => {
  const tree = apply(
    p(img("u1"), { type: "text", value: "caption" }, img("u2")),
  );
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("p");
});

test("keeps paragraphs mixing images with other elements", () => {
  const strong: Element = {
    type: "element",
    tagName: "strong",
    properties: {},
    children: [{ type: "text", value: "hi" }],
  };
  const tree = apply(p(img("u1"), strong, img("u2")));
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("p");
});

test("does not touch images nested inside anchors", () => {
  const a: Element = {
    type: "element",
    tagName: "a",
    properties: { href: "u1" },
    children: [img("u1")],
  };
  const tree = apply(p(a, img("u2")));
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("p");
});

test("preserves existing class names when renaming", () => {
  const node = p(img("u1"), img("u2"));
  node.properties.className = ["keep-me"];
  const tree = apply(node);
  const renamed = tree.children[0] as Element;
  expect(renamed.tagName).toBe("div");
  expect(renamed.properties.className).toEqual(["keep-me", "md-image-gallery"]);
});

test("ignores non-paragraph elements containing images", () => {
  const div: Element = {
    type: "element",
    tagName: "div",
    properties: {},
    children: [img("u1"), img("u2")],
  };
  const tree = apply(div);
  const node = tree.children[0] as Element;
  expect(node.tagName).toBe("div");
  expect(node.properties.className).toBeUndefined();
});

test("walks into containers and converts image paragraphs inside them", () => {
  const li: Element = {
    type: "element",
    tagName: "li",
    properties: {},
    children: [p(img("u1"), img("u2"))],
  };
  const list: Element = {
    type: "element",
    tagName: "ul",
    properties: {},
    children: [li],
  };
  const tree = apply(list);
  const listNode = tree.children[0] as Element;
  const liNode = listNode.children[0] as Element;
  const pNode = liNode.children[0] as Element;
  expect(pNode.tagName).toBe("div");
  expect(pNode.properties.className).toContain("md-image-gallery");
});
