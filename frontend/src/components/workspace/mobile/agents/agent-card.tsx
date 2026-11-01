"use client";

import {
  BotIcon,
  MessageSquareIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "@/components/ui/card";
import { type Agent, useDeleteAgent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

/**
 * One agent in the mobile gallery (prototype ⑥) — the single-column form of the
 * desktop `AgentCard`.
 *
 * The data and the actions are the desktop card's: the same `Agent` shape, the
 * same `useDeleteAgent()` mutation and the same `toast` copy. What changes is
 * the shape, and two of those changes are the point of the mobile screen:
 *
 * - the card **is** a chat entry. The desktop card reaches the agent through a
 *   single "chat" button; here the whole upper block is a link to the same
 *   public path, so a thumb does not have to find a small target.
 * - every control clears 44px. The desktop's edit/delete buttons are 32px icon
 *   buttons revealed on hover-adjacent layout, which is a mis-tap on a phone.
 *
 * Both destinations are the **public** paths: `/workspace/agents/...` for the
 * chat (the same address the desktop uses, so the middleware lands a phone on
 * the mobile chat tree) and `/agents/<name>/edit` for the editor. `<Link>`
 * issues an RSC request the middleware rewrites, so neither writes `/m/` into
 * the address bar (plan §1.2.1).
 */
export function MobileAgentCard({ agent }: { agent: Agent }) {
  const { t } = useI18n();
  const deleteAgent = useDeleteAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const chatHref = `/workspace/agents/${agent.name}/chats/new`;
  const editHref = `/agents/${agent.name}/edit`;
  // Two groups, two tones — the same distinction the desktop card draws. They
  // are unlabelled there too, so parity is exact; the editor is where the
  // whitelists are spelled out.
  const capabilities = [
    ...(agent.tool_groups ?? []).map((value) => ({
      value,
      variant: "outline" as const,
    })),
    ...(agent.skills ?? []).map((value) => ({
      value,
      variant: "secondary" as const,
    })),
  ];

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <Card data-testid="mobile-agent-card" data-agent-name={agent.name}>
        <Link
          href={chatHref}
          aria-label={`${t.agents.chat}: ${agent.name}`}
          data-testid={`mobile-agent-open-${agent.name}`}
          className="active:bg-accent/50 block rounded-t-xl"
        >
          <CardHeader className="pb-2">
            <div className="flex min-w-0 items-start gap-3">
              <div className="bg-primary/10 text-primary flex size-10 shrink-0 items-center justify-center rounded-lg">
                <BotIcon aria-hidden="true" className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-base font-semibold">{agent.name}</p>
                {agent.model && (
                  <Badge
                    variant="secondary"
                    className="mt-1 block max-w-full truncate text-xs"
                  >
                    {agent.model}
                  </Badge>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent className="pb-3">
            {agent.description && (
              <p className="text-muted-foreground line-clamp-3 text-sm">
                {agent.description}
              </p>
            )}
            {capabilities.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {capabilities.map(({ value, variant }) => (
                  <Badge
                    key={`${variant}:${value}`}
                    variant={variant}
                    className="max-w-full text-xs"
                  >
                    <span className="truncate">{value}</span>
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Link>

        <CardFooter className="flex items-center gap-2 pt-0">
          <Button asChild className="min-h-11 flex-1 text-base">
            <Link
              href={chatHref}
              data-testid={`mobile-agent-chat-${agent.name}`}
            >
              <MessageSquareIcon aria-hidden="true" className="size-4" />
              {t.agents.chat}
            </Link>
          </Button>
          <Button asChild variant="outline" size="icon" className="size-11">
            <Link
              href={editHref}
              aria-label={`${t.agents.edit}: ${agent.name}`}
              data-testid={`mobile-agent-edit-${agent.name}`}
            >
              <PencilIcon aria-hidden="true" className="size-4" />
            </Link>
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="text-destructive size-11"
            aria-label={`${t.agents.delete}: ${agent.name}`}
            data-testid={`mobile-agent-delete-${agent.name}`}
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2Icon aria-hidden="true" className="size-4" />
          </Button>
        </CardFooter>
      </Card>

      {/* Same confirmation step the desktop card uses, as an alert dialog: on a
          phone the destructive target sits next to two benign ones and the
          gallery row is gone the moment it is tapped. */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.agents.delete}</AlertDialogTitle>
            <AlertDialogDescription>
              {t.agents.deleteConfirm}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteAgent.isPending}>
              {t.common.cancel}
            </AlertDialogCancel>
            <AlertDialogAction
              data-testid="mobile-agent-delete-confirm"
              disabled={deleteAgent.isPending}
              onClick={(event) => {
                // Keep the dialog up while the request runs; the mutation's
                // outcome decides whether it closes.
                event.preventDefault();
                void handleDelete();
              }}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
