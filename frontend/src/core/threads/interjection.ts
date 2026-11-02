"use client";

import { getBackendBaseURL } from "@/core/config";

/**
 * Thread interjection client (docs/research/2026-11-01-thread-interjection/plan-b.md).
 *
 * While a run is in-flight, a new user message is queued into the running
 * agent (202 + queued interjection) instead of starting a new run (which the
 * gateway would reject with 409 when interjection is disabled).
 */

export type PendingInterjection = {
  id: string;
  thread_id: string;
  run_id: string;
  text: string;
  source: string;
  seq: number;
  created_at: string;
  applied: boolean;
  /** Local only: set when the run ended and the item was merged into this follow-up run. */
  auto_run_run_id?: string | null;
  /** Local only: merged user-message text of the follow-up run (for bubble pruning). */
  auto_run_input?: string | null;
};

export type CancelInterjectionResult =
  | "cancelled"
  | "already_applied"
  | "not_found";

/** Raised when the gateway has no in-flight run to interject into (409). */
export class InterjectionNoActiveRunError extends Error {
  constructor(message = "No in-flight run to interject into") {
    super(message);
    this.name = "InterjectionNoActiveRunError";
  }
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body?.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export async function createInterjection(
  threadId: string,
  text: string,
  source = "web",
): Promise<PendingInterjection> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/interjections`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ text, source }),
    },
  );
  if (response.status === 409) {
    throw new InterjectionNoActiveRunError(await readErrorDetail(response));
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  return (await response.json()) as PendingInterjection;
}

export async function cancelInterjection(
  threadId: string,
  interjectionId: string,
): Promise<CancelInterjectionResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/interjections/${encodeURIComponent(interjectionId)}`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  if (response.status === 204) {
    return "cancelled";
  }
  if (response.status === 409) {
    return "already_applied";
  }
  return "not_found";
}

/**
 * Run-end follow-up: the worker ended the run with queued messages still
 * unconsumed and merged them into one new run. Marks the affected local
 * bubbles so the "please re-send" drop toast doesn't fire and pruning can
 * match the merged follow-up input text once it lands in the transcript.
 */
export type InterjectionAutoRunEvent = {
  new_run_id?: string | null;
  input?: string;
  interjections?: { id: string }[];
};

export function applyAutoRunEvent(
  items: PendingInterjection[],
  event: InterjectionAutoRunEvent,
): PendingInterjection[] {
  const ids = new Set((event.interjections ?? []).map((item) => item.id));
  if (ids.size === 0) {
    return items;
  }
  return items.map((item) =>
    ids.has(item.id)
      ? {
          ...item,
          auto_run_run_id: event.new_run_id ?? null,
          auto_run_input: event.input ?? null,
        }
      : item,
  );
}

/**
 * Drop queued bubbles whose text has landed in the transcript as a real
 * HumanMessage: applied items match their own text, auto-run items match the
 * merged follow-up input text.
 */
export function pruneLandedInterjections(
  items: PendingInterjection[],
  humanTexts: Set<string>,
): PendingInterjection[] {
  return items.filter((item) => {
    if (item.applied && humanTexts.has(item.text)) {
      return false;
    }
    if (
      item.auto_run_run_id &&
      item.auto_run_input != null &&
      humanTexts.has(item.auto_run_input)
    ) {
      return false;
    }
    return true;
  });
}

/** True when a queued bubble still belongs to the live run (no drop toast). */
export function hasUnclaimedQueuedInterjections(
  items: PendingInterjection[],
): boolean {
  return items.some((item) => !item.applied && !item.auto_run_run_id);
}
