import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { StreamdownProps } from "streamdown";

import {
  rehypeGroupImagesIntoGallery,
  rehypeSplitWordsIntoSpans,
} from "../rehype";

const katexOptions = {
  output: "html",
  throwOnError: false,
  strict: false,
} as const;

export const streamdownPlugins = {
  remarkPlugins: [
    remarkGfm,
    [remarkMath, { singleDollarTextMath: true }],
  ] as StreamdownProps["remarkPlugins"],
  rehypePlugins: [
    rehypeRaw,
    [rehypeKatex, katexOptions],
    rehypeGroupImagesIntoGallery,
  ] as StreamdownProps["rehypePlugins"],
};

export const streamdownPluginsWithWordAnimation = {
  remarkPlugins: [
    remarkGfm,
    [remarkMath, { singleDollarTextMath: true }],
  ] as StreamdownProps["remarkPlugins"],
  rehypePlugins: [
    [rehypeKatex, katexOptions],
    // Gallery grouping must run before word-splitting: split leaves
    // whitespace-only spans inside the paragraph that would block detection.
    rehypeGroupImagesIntoGallery,
    rehypeSplitWordsIntoSpans,
  ] as StreamdownProps["rehypePlugins"],
};

export const streamdownPluginsWithoutRawHtml = {
  remarkPlugins: streamdownPlugins.remarkPlugins,
  rehypePlugins: streamdownPlugins.rehypePlugins?.filter(
    (p) => p !== rehypeRaw,
  ) as StreamdownProps["rehypePlugins"],
};

// Plugins for reasoning/thinking content — derived from streamdownPlugins but without rehypeRaw,
// to prevent LLM-hallucinated HTML tags (e.g. <simd>) from being rendered as DOM elements.
export const reasoningPlugins = streamdownPluginsWithoutRawHtml;
