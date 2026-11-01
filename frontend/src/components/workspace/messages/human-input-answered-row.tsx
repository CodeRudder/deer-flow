"use client";

import { ArrowRightIcon, CheckCircle2Icon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import {
  formatHumanInputAnsweredValue,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";

export function HumanInputAnsweredRow({
  request,
  response,
}: {
  request: HumanInputRequest;
  response: HumanInputResponse;
}) {
  const { t } = useI18n();
  const answer = formatHumanInputAnsweredValue(request, response);

  return (
    <div
      className="bg-muted/40 text-muted-foreground flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm"
      data-testid="human-input-answered-row"
    >
      <CheckCircle2Icon aria-hidden className="text-primary size-4 shrink-0" />
      <span className="shrink-0 font-medium">{t.humanInput.answered}</span>
      <span className="min-w-0 truncate" title={request.question}>
        {request.question}
      </span>
      <ArrowRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
      <span
        className="text-foreground min-w-0 flex-1 truncate font-medium"
        title={answer}
      >
        {answer}
      </span>
    </div>
  );
}
