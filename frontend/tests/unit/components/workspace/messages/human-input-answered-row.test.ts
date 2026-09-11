import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { HumanInputAnsweredRow } from "@/components/workspace/messages/human-input-answered-row";
import { I18nContext } from "@/core/i18n/context";
import type {
  HumanInputRequest,
  HumanInputResponse,
} from "@/core/messages/human-input";

const request: HumanInputRequest = {
  version: 1,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-abc",
  tool_call_id: "call-abc",
  question: "Which environment should I deploy to?",
  input_mode: "single_choice",
  options: [
    { id: "option-1", label: "development", value: "development" },
    { id: "option-2", label: "staging", value: "staging" },
  ],
};

function optionResponse(optionId: string, value: string): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: "ask_clarification",
    request_id: request.request_id,
    response_kind: "option",
    option_id: optionId,
    value,
  };
}

function textResponse(value: string): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: "ask_clarification",
    request_id: request.request_id,
    response_kind: "text",
    value,
  };
}

function renderRow(
  req: HumanInputRequest = request,
  response: HumanInputResponse,
) {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      {
        // The fork's I18nContext has no `t`; it is resolved from locale.
        value: {
          locale: "en-US",
          setLocale: () => undefined,
        },
      },
      createElement(HumanInputAnsweredRow, { request: req, response }),
    ),
  );
}

describe("HumanInputAnsweredRow", () => {
  it("renders the answered label, the question, and the answer", () => {
    const html = renderRow(request, optionResponse("option-2", "staging"));

    expect(html).toContain("Answered");
    expect(html).toContain("Which environment should I deploy to?");
    expect(html).toContain("staging");
  });

  it("prefers the chosen option's label over the raw value", () => {
    const req: HumanInputRequest = {
      ...request,
      options: [{ id: "option-1", label: "方案一：继续", value: "plan-a" }],
    };
    const html = renderRow(req, optionResponse("option-1", "plan-a"));

    expect(html).toContain("方案一：继续");
    expect(html).not.toContain("plan-a");
  });

  it("strips the form values JSON block from text answers", () => {
    const req: HumanInputRequest = {
      ...request,
      input_mode: "form",
      fields: [{ name: "env", label: "Env", type: "select", required: true }],
    };
    const html = renderRow(
      req,
      textResponse('Env: staging [values: {"env":"staging"}]'),
    );

    expect(html).toContain("Env: staging");
    expect(html).not.toContain("[values");
  });
});
