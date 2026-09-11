import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";

import {
  buildHumanInputFormSubmissionValue,
  buildHumanInputFormSummary,
  buildHumanInputResponseText,
  buildInitialHumanInputFormValues,
  createHumanInputOptionResponse,
  createHumanInputTextResponse,
  deriveHumanInputThreadState,
  extractHumanInputRequest,
  extractHumanInputResponse,
  hasOpenHumanInputRequest,
  parseHumanInputRequest,
  parseHumanInputResponse,
  readHumanInputFormValue,
  shouldClearPendingHumanInputOnThreadError,
  type HumanInputField,
  type HumanInputFormValue,
} from "@/core/messages/human-input";

function toolMessageWithRequest(
  request: Record<string, unknown>,
  id = "tool-1",
): Message {
  return {
    id,
    type: "tool",
    content: "fallback text",
    artifact: { human_input: request },
  } as unknown as Message;
}

function humanWithResponse(
  response: Record<string, unknown>,
  value = "my answer",
  hide = true,
): Message {
  const additional_kwargs: Record<string, unknown> = {
    human_input_response: response,
  };
  if (hide) additional_kwargs.hide_from_ui = true;
  return {
    id: "human-response-1",
    type: "human",
    content: value,
    additional_kwargs,
  } as unknown as Message;
}

function hiddenHumanWithResponse(
  response: Record<string, unknown>,
  value = "my answer",
): Message {
  return humanWithResponse(response, value, true);
}

const choiceRequestPayload = {
  version: 1,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-1",
  tool_call_id: "call-1",
  clarification_type: "approach_choice",
  question: "Which environment?",
  input_mode: "choice_with_other",
  options: [
    { id: "option-1", label: "dev", value: "dev" },
    { id: "option-2", label: "staging", value: "staging" },
  ],
};

const formFieldsPayload = [
  {
    name: "env",
    label: "Env",
    type: "select",
    required: true,
    options: [
      { id: "env-option-1", label: "dev", value: "dev" },
      { id: "env-option-2", label: "prod", value: "prod" },
    ],
  },
  { name: "project", label: "Project", type: "text", required: true },
  { name: "rollback", label: "Rollback", type: "checkbox", required: false },
];

const formRequestPayload = {
  version: 2,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-2",
  question: "Configure the deployment",
  input_mode: "form",
  fields: formFieldsPayload,
};

describe("parseHumanInputRequest", () => {
  test("accepts a valid v1 choice request", () => {
    const request = parseHumanInputRequest(choiceRequestPayload);
    expect(request).not.toBeNull();
    expect(request!.input_mode).toBe("choice_with_other");
    expect(request!.options).toHaveLength(2);
  });

  test("accepts a valid v2 form request", () => {
    const request = parseHumanInputRequest(formRequestPayload);
    expect(request).not.toBeNull();
    expect(request!.version).toBe(2);
    expect(request!.fields).toHaveLength(3);
  });

  test("rejects broken payloads", () => {
    expect(parseHumanInputRequest(null)).toBeNull();
    expect(parseHumanInputRequest("text")).toBeNull();
    expect(parseHumanInputRequest({})).toBeNull();
    expect(
      parseHumanInputRequest({ ...choiceRequestPayload, kind: "other" }),
    ).toBeNull();
    expect(
      parseHumanInputRequest({ ...choiceRequestPayload, request_id: "" }),
    ).toBeNull();
    // mode without options
    expect(
      parseHumanInputRequest({
        ...choiceRequestPayload,
        options: undefined,
      }),
    ).toBeNull();
    // option with empty value breaks Radix Select
    expect(
      parseHumanInputRequest({
        ...choiceRequestPayload,
        options: [{ id: "option-1", label: "dev", value: "" }],
      }),
    ).toBeNull();
    // duplicate option values
    expect(
      parseHumanInputRequest({
        ...choiceRequestPayload,
        options: [
          { id: "option-1", label: "a", value: "a" },
          { id: "option-2", label: "b", value: "a" },
        ],
      }),
    ).toBeNull();
  });

  test("enforces version/mode binding", () => {
    // form mode with version 1 is invalid
    expect(
      parseHumanInputRequest({ ...formRequestPayload, version: 1 }),
    ).toBeNull();
    // non-form mode with version 2 is invalid
    expect(
      parseHumanInputRequest({ ...choiceRequestPayload, version: 2 }),
    ).toBeNull();
  });

  test("rejects forms with reserved or duplicate field names", () => {
    expect(
      parseHumanInputRequest({
        ...formRequestPayload,
        fields: [
          { name: "__proto__", label: "P", type: "text", required: false },
        ],
      }),
    ).toBeNull();
    expect(
      parseHumanInputRequest({
        ...formRequestPayload,
        fields: [
          { name: "a", label: "A", type: "text", required: false },
          { name: "a", label: "A2", type: "text", required: false },
        ],
      }),
    ).toBeNull();
  });

  test("rejects select fields without options", () => {
    expect(
      parseHumanInputRequest({
        ...formRequestPayload,
        fields: [
          { name: "env", label: "Env", type: "select", required: false },
        ],
      }),
    ).toBeNull();
  });

  test("rejects form mode with empty fields", () => {
    expect(
      parseHumanInputRequest({ ...formRequestPayload, fields: [] }),
    ).toBeNull();
  });
});

describe("parseHumanInputResponse", () => {
  const textResponse = {
    version: 1,
    kind: "human_input_response",
    source: "ask_clarification",
    request_id: "clarification:call-1",
    response_kind: "text",
    value: "staging",
  };

  test("accepts valid text and option responses", () => {
    expect(parseHumanInputResponse(textResponse)?.response_kind).toBe("text");
    expect(
      parseHumanInputResponse({
        ...textResponse,
        response_kind: "option",
        option_id: "option-2",
      }),
    ).toMatchObject({ response_kind: "option", option_id: "option-2" });
  });

  test("rejects broken responses", () => {
    expect(parseHumanInputResponse(null)).toBeNull();
    expect(parseHumanInputResponse({})).toBeNull();
    expect(
      parseHumanInputResponse({ ...textResponse, value: "  " }),
    ).toBeNull();
    expect(parseHumanInputResponse({ ...textResponse, version: 2 })).toBeNull();
    // option kind without option_id
    expect(
      parseHumanInputResponse({ ...textResponse, response_kind: "option" }),
    ).toBeNull();
    // unknown response kind
    expect(
      parseHumanInputResponse({ ...textResponse, response_kind: "form" }),
    ).toBeNull();
  });
});

describe("extractHumanInputRequest / extractHumanInputResponse", () => {
  test("extracts request from tool message artifact", () => {
    const request = extractHumanInputRequest(
      toolMessageWithRequest(choiceRequestPayload),
    );
    expect(request?.request_id).toBe("clarification:call-1");
    // non-tool messages carry no request
    expect(
      extractHumanInputRequest({
        id: "ai-1",
        type: "ai",
        content: "",
      } as Message),
    ).toBeNull();
  });

  test("extracts response from hidden human message", () => {
    const response = extractHumanInputResponse(
      hiddenHumanWithResponse({
        version: 1,
        kind: "human_input_response",
        source: "ask_clarification",
        request_id: "clarification:call-1",
        response_kind: "option",
        option_id: "option-1",
        value: "dev",
      }),
    );
    expect(response?.response_kind).toBe("option");
    // non-human messages carry no response
    expect(
      extractHumanInputResponse({
        id: "ai-1",
        type: "ai",
        content: "",
      } as Message),
    ).toBeNull();
  });
});

describe("deriveHumanInputThreadState", () => {
  const optionResponse = {
    version: 1,
    kind: "human_input_response",
    source: "ask_clarification",
    request_id: "clarification:call-1",
    response_kind: "option",
    option_id: "option-2",
    value: "staging",
  };

  test("open request without answer yields latestOpenRequestId", () => {
    const state = deriveHumanInputThreadState([
      toolMessageWithRequest(choiceRequestPayload),
    ]);
    expect(state.latestOpenRequestId).toBe("clarification:call-1");
  });

  test("structured response closes the request", () => {
    const state = deriveHumanInputThreadState([
      toolMessageWithRequest(choiceRequestPayload),
      hiddenHumanWithResponse(optionResponse),
    ]);
    expect(state.latestOpenRequestId).toBeNull();
    expect(state.answeredResponses.get("clarification:call-1")).toMatchObject({
      response_kind: "option",
    });
  });

  // derive is visibility-agnostic: both the hidden reply (the fork's actual
  // protocol) and a visible metadata-carrying message close the request with
  // the clean structured value.
  test("visible response message with metadata closes the request", () => {
    const state = deriveHumanInputThreadState([
      toolMessageWithRequest(choiceRequestPayload),
      humanWithResponse(
        optionResponse,
        'For your clarification "Which env?", my answer is: dev',
        false,
      ),
    ]);
    expect(state.latestOpenRequestId).toBeNull();
    expect(state.answeredResponses.get("clarification:call-1")).toMatchObject({
      response_kind: "option",
      value: "staging",
    });
  });

  test("legacy plain human reply closes the latest unanswered request", () => {
    const state = deriveHumanInputThreadState([
      toolMessageWithRequest(choiceRequestPayload),
      {
        id: "human-legacy",
        type: "human",
        content: "staging please",
      } as Message,
    ]);
    expect(state.latestOpenRequestId).toBeNull();
    expect(state.answeredResponses.get("clarification:call-1")).toMatchObject({
      response_kind: "text",
      value: "staging please",
    });
  });

  test("visible messages that are not human do not close requests", () => {
    const state = deriveHumanInputThreadState([
      toolMessageWithRequest(choiceRequestPayload),
      { id: "ai-1", type: "ai", content: "working on it" } as Message,
    ]);
    expect(state.latestOpenRequestId).toBe("clarification:call-1");
  });

  test("answer before its request (out of order) does not close it", () => {
    const state = deriveHumanInputThreadState([
      hiddenHumanWithResponse(optionResponse),
      toolMessageWithRequest(choiceRequestPayload),
    ]);
    expect(state.latestOpenRequestId).toBe("clarification:call-1");
  });

  test("hasOpenHumanInputRequest reflects thread state", () => {
    expect(
      hasOpenHumanInputRequest([toolMessageWithRequest(choiceRequestPayload)]),
    ).toBe(true);
    expect(
      hasOpenHumanInputRequest([
        toolMessageWithRequest(choiceRequestPayload),
        hiddenHumanWithResponse(optionResponse),
      ]),
    ).toBe(false);
  });

  test("survives malformed request payloads", () => {
    expect(
      hasOpenHumanInputRequest([toolMessageWithRequest({ broken: true })]),
    ).toBe(false);
  });
});

describe("shouldClearPendingHumanInputOnThreadError", () => {
  test("clears only when there are pending requests and a new error", () => {
    expect(
      shouldClearPendingHumanInputOnThreadError({
        currentError: new Error("x"),
        pendingRequestCount: 1,
        previousError: undefined,
      }),
    ).toBe(true);
    expect(
      shouldClearPendingHumanInputOnThreadError({
        currentError: new Error("x"),
        pendingRequestCount: 0,
        previousError: undefined,
      }),
    ).toBe(false);
    const same = new Error("x");
    expect(
      shouldClearPendingHumanInputOnThreadError({
        currentError: same,
        pendingRequestCount: 1,
        previousError: same,
      }),
    ).toBe(false);
  });
});

describe("form value helpers", () => {
  test("readHumanInputFormValue ignores prototype chain", () => {
    const values: Record<string, HumanInputFormValue> = {};
    expect(readHumanInputFormValue(values, "toString")).toBeUndefined();
    expect(readHumanInputFormValue({ a: 1 }, "a")).toBe(1);
  });

  test("buildInitialHumanInputFormValues seeds checkboxes", () => {
    const fields = (formRequestPayload.fields ?? []) as HumanInputField[];
    const values = buildInitialHumanInputFormValues(fields);
    expect(values.rollback).toBe(false);
    expect(values.env).toBeUndefined();
  });
});

describe("form submission builders", () => {
  const request = parseHumanInputRequest(formRequestPayload)!;

  test("summary joins filled fields and skips empty ones", () => {
    const summary = buildHumanInputFormSummary(request, {
      env: "prod",
      project: "",
      rollback: true,
    });
    expect(summary).toBe("Env: prod; Rollback: yes");
  });

  test("submission appends machine-readable JSON keyed by field name", () => {
    const submission = buildHumanInputFormSubmissionValue(request, {
      env: "prod",
      project: "deer",
      rollback: false,
    });
    expect(submission).toBe(
      'Env: prod; Project: deer; Rollback: no [values: {"env":"prod","project":"deer","rollback":false}]',
    );
  });

  test("multi-select values are joined with commas", () => {
    const summary = buildHumanInputFormSummary(request, {
      env: ["dev", "prod"],
    });
    expect(summary).toBe("Env: dev, prod");
  });
});

describe("response factories", () => {
  test("createHumanInputOptionResponse", () => {
    const request = parseHumanInputRequest(choiceRequestPayload)!;
    const response = createHumanInputOptionResponse(request, {
      id: "option-2",
      label: "staging",
      value: "staging",
    });
    expect(response).toMatchObject({
      version: 1,
      kind: "human_input_response",
      source: "ask_clarification",
      request_id: "clarification:call-1",
      response_kind: "option",
      option_id: "option-2",
      value: "staging",
    });
  });

  test("createHumanInputTextResponse and reply text", () => {
    const request = parseHumanInputRequest(choiceRequestPayload)!;
    const response = createHumanInputTextResponse(request, "staging, careful");
    expect(response.response_kind).toBe("text");
    expect(buildHumanInputResponseText(request, response)).toBe(
      'For your clarification "Which environment?", my answer is: staging, careful',
    );
  });
});
