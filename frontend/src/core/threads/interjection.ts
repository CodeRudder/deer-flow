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
