"use client";

import { useQueryClient } from "@tanstack/react-query";
import { PlusIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { MobileAgentCard } from "@/components/workspace/mobile/agents/agent-card";
import { MobileAgentCreateChoiceSheet } from "@/components/workspace/mobile/agents/create-option-sheet";
import { useAgents } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Mobile agent gallery — the shell's second root screen (prototype ⑥).
 *
 * The desktop gallery is the same data in a 1/2/3/4-column grid
 * (`agent-gallery.tsx`); a 390px column takes one card per row, so the grid
 * collapses and the header becomes a title plus a `＋` that opens the same
 * two-option choice the desktop dialog offers — as a bottom sheet
 * (`create-option-sheet.tsx`).
 *
 * The data layer is unchanged: `useAgents()` is the desktop's hook, so the
 * query key and the cache are shared and `useCreateAgent` / `useUpdateAgent` /
 * `useDeleteAgent` invalidations refresh this list for free.
 *
 * Loading, failure and empty are three distinct branches, following the thread
 * list's rule (`m/(app)/(tabbed)/workspace/page.tsx`): a gateway that is down
 * must not read as "you have no agents".
 */
export default function MobileAgentsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { agents, isLoading, error } = useAgents();
  const [choiceOpen, setChoiceOpen] = useState(false);

  useEffect(() => {
    document.title = `${t.agents.title} - ${t.pages.appName}`;
  }, [t.agents.title, t.pages.appName]);

  return (
    <div className="flex min-h-full flex-col">
      <header className="bg-background/95 sticky top-0 z-10 flex items-center gap-1 border-b px-3 py-1.5 backdrop-blur supports-backdrop-filter:backdrop-blur">
        <h1 className="min-w-0 flex-1 truncate px-1 text-lg font-semibold">
          {t.agents.title}
        </h1>
        <button
          type="button"
          aria-label={t.agents.newAgent}
          data-testid="mobile-new-agent"
          onClick={() => setChoiceOpen(true)}
          className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
        >
          <PlusIcon aria-hidden="true" className="size-5" />
        </button>
      </header>

      {/* One card per row: the desktop grid's `grid-cols-1 sm:grid-cols-2 …`
          collapses to a plain column, so no card can ever share a row. */}
      <div
        data-testid="mobile-agent-list"
        className="flex min-h-0 flex-1 flex-col gap-3 px-4 py-4 pb-[calc(env(safe-area-inset-bottom)+1rem)]"
      >
        {isLoading ? (
          <p
            data-testid="mobile-agents-loading"
            className="text-muted-foreground px-4 py-10 text-center text-sm"
          >
            {t.common.loading}
          </p>
        ) : error ? (
          <div
            data-testid="mobile-agents-load-error"
            role="alert"
            className="flex flex-col items-center gap-3 px-4 py-10 text-center"
          >
            {/* `loadFailed`, not `loadDetailFailed`: the latter says "showing
                stale data", which is wrong here — nothing loaded at all. */}
            <p className="text-muted-foreground text-sm">
              {t.agents.loadFailed}
            </p>
            <Button
              variant="outline"
              className="min-h-11 text-base"
              onClick={() =>
                void queryClient.invalidateQueries({ queryKey: ["agents"] })
              }
            >
              {t.common.retry}
            </Button>
          </div>
        ) : agents.length === 0 ? (
          <div
            data-testid="mobile-agents-empty"
            className="flex flex-col items-center gap-3 px-4 py-12 text-center"
          >
            <div className="bg-muted flex size-14 items-center justify-center rounded-full">
              <PlusIcon
                aria-hidden="true"
                className="text-muted-foreground size-6"
              />
            </div>
            <div>
              <p className="font-medium">{t.agents.emptyTitle}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {t.agents.emptyDescription}
              </p>
            </div>
            <Button
              className="min-h-11 text-base"
              data-testid="mobile-agents-empty-create"
              onClick={() => setChoiceOpen(true)}
            >
              {t.agents.newAgent}
            </Button>
          </div>
        ) : (
          agents.map((agent) => (
            <MobileAgentCard key={agent.name} agent={agent} />
          ))
        )}
      </div>

      <MobileAgentCreateChoiceSheet
        open={choiceOpen}
        onOpenChange={setChoiceOpen}
      />
    </div>
  );
}
