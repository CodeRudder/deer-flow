import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import {
  ModelSelector,
  ModelSelectorContent,
} from "@/components/ai-elements/model-selector";

/**
 * `ModelSelectorContent` gained an optional `description` (T26) so a caller
 * that is not a model picker — the artifact screen's file switcher — does not
 * announce itself as one. The prop is additive, and these two cases are what
 * says so: the desktop's three call sites pass no description, so the registry
 * sentence has to come out byte for byte.
 */

/** The registry's own sentence, which the desktop reads out. */
const DEFAULT_DESCRIPTION = "Search and choose a model for this conversation.";

function renderDialog(description?: string) {
  return render(
    <ModelSelector open>
      <ModelSelectorContent {...(description ? { description } : {})}>
        <span>dialog body</span>
      </ModelSelectorContent>
    </ModelSelector>,
  );
}

afterEach(cleanup);

describe("ModelSelectorContent", () => {
  it("keeps the registry description verbatim when none is passed", () => {
    renderDialog();

    expect(screen.getByText(DEFAULT_DESCRIPTION)).toBeTruthy();
    // The title default is untouched too — the two are the dialog's whole
    // accessible name.
    expect(screen.getByText("Model Selector")).toBeTruthy();
  });

  it("uses the caller's description instead of the model sentence", () => {
    renderDialog("输入关键字筛选文件");

    expect(screen.getByText("输入关键字筛选文件")).toBeTruthy();
    expect(screen.queryByText(DEFAULT_DESCRIPTION)).toBeNull();
    // `description` must not leak onto the dialog element as a DOM attribute.
    expect(document.querySelector("[description]")).toBeNull();
  });
});
