"use client";

import { MessageSquareIcon, PenLineIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { AgentCreateOption } from "@/components/workspace/agents/agent-create-sheet";
import { useI18n } from "@/core/i18n/hooks";

import "./agents-surface.css";

/**
 * The two ways to create an agent, as a bottom sheet (prototype ⑥'s `＋`).
 *
 * The desktop shows this choice in a centred dialog and then puts the manual
 * form in a right-side Sheet. On a phone the dialog becomes a bottom sheet —
 * reachable with a thumb — and the manual form becomes a full screen of its
 * own (`/agents/new`), because SOUL.md is a long document and a sheet would
 * leave it a few lines tall.
 *
 * The two rows reuse the desktop's `AgentCreateOption` unchanged: same copy,
 * same icon-in-a-tile layout, same click contract (it is a plain button, so it
 * already clears the touch floor at `p-4` with two lines of text).
 */
export function MobileAgentCreateChoiceSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const router = useRouter();

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        data-testid="mobile-agent-create-sheet"
        className="mobile-agents-sheet gap-3 pb-[calc(env(safe-area-inset-bottom)+1rem)]"
      >
        <SheetHeader>
          <SheetTitle className="text-base">{t.agents.newAgent}</SheetTitle>
          <SheetDescription>
            {t.agents.createChoiceDescription}
          </SheetDescription>
        </SheetHeader>
        {/* Wrapped so the close-button rule in `agents-surface.css` cannot
            reach the option buttons. */}
        <div className="flex flex-col gap-2 px-4">
          <AgentCreateOption
            icon={<PenLineIcon className="size-4" />}
            title={t.agents.createChoiceManualTitle}
            description={t.agents.createChoiceManualDescription}
            onClick={() => go("/agents/new")}
          />
          <AgentCreateOption
            icon={<MessageSquareIcon className="size-4" />}
            title={t.agents.createChoiceChatTitle}
            description={t.agents.createChoiceChatDescription}
            // The chat-based bootstrap flow is a conversation, so it stays on
            // the desktop's path — a phone just gets the mobile chat tree for
            // it through the middleware rewrite (plan G4).
            onClick={() => go("/workspace/agents/new")}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
