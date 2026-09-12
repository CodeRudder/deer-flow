"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, SaveIcon } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { MODEL_DEFAULT_VALUE } from "@/components/workspace/agents/agent-edit-sheet";
import {
  MobileAgentDescriptionField,
  MobileAgentSoulField,
} from "@/components/workspace/mobile/agents/agent-form-fields";
import {
  MobileAgentModelPicker,
  modelOptionsWithCurrent,
} from "@/components/workspace/mobile/agents/agent-model-picker";
import { MobileAgentWhitelistRow } from "@/components/workspace/mobile/agents/whitelist-row";
import { MobileAuthLabel } from "@/components/workspace/mobile/auth-field";
import { type Agent, useAgent, useUpdateAgent } from "@/core/agents";
import {
  AgentLegacyLayoutError,
  AgentsApiDisabledError,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";

import "@/components/workspace/mobile/agents/agents-surface.css";

/**
 * Agent editor, as a full screen (prototype ⑥'s 编辑).
 *
 * The desktop edits the same three fields in a right-side drawer
 * (`agent-edit-sheet.tsx`). Same rules, restated in a page:
 *
 * - the detail is re-fetched by `useAgent(name)` and is the form's **only**
 *   baseline — there is no gallery object behind it, so the drawer's
 *   "adopt the fresh detail only while the user has not typed yet" dance has no
 *   reason to exist here. The form is not mounted until the detail lands, which
 *   is what keeps the read-only section below honest: it renders the fetched
 *   `skills` / `tool_groups`, not a stale list row.
 * - only SOUL.md, description and model are editable, and the request sends
 *   exactly those three keys — the backend merges by `model_fields_set`, so
 *   sending `tool_groups`/`skills` from a read-only section would rewrite them.
 * - `tool_groups` / `skills` keep their three-state semantics (null = inherit
 *   all, `[]` = none, list = whitelist) and are rendered read-only.
 * - closing with unsaved changes asks first; a 409 from a legacy shared-layout
 *   agent degrades the screen to read-only instead of retrying forever.
 */
export default function MobileEditAgentPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const params = useParams<{ agent_name: string }>();
  const agentName = params.agent_name;
  const { agent, isLoading, error } = useAgent(agentName);

  useEffect(() => {
    document.title = `${t.agents.editTitle} - ${t.pages.appName}`;
  }, [t.agents.editTitle, t.pages.appName]);

  if (isLoading) {
    return (
      <EditPageShell title={agentName}>
        <p
          data-testid="mobile-agent-edit-loading"
          className="text-muted-foreground px-4 py-10 text-center text-sm"
        >
          {t.common.loading}
        </p>
      </EditPageShell>
    );
  }

  if (error || !agent) {
    return (
      <EditPageShell title={agentName}>
        <div
          role="alert"
          data-testid="mobile-agent-edit-load-error"
          className="flex flex-col items-center gap-3 px-4 py-10 text-center"
        >
          <p className="text-muted-foreground text-sm">
            {t.agents.loadDetailFailed}
          </p>
          <Button
            variant="outline"
            className="min-h-11 text-base"
            onClick={() =>
              void queryClient.invalidateQueries({
                queryKey: ["agents", agentName],
              })
            }
          >
            {t.common.retry}
          </Button>
        </div>
      </EditPageShell>
    );
  }

  return <MobileAgentEditForm key={agent.name} agent={agent} />;
}

/**
 * Chrome for the two states that have no form yet (loading / failed detail):
 * the back link is the public `/agents` path, so a phone lands back on the
 * mobile gallery through the middleware rather than on `/m/agents`.
 */
function EditPageShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-full flex-col">
      <EditHeader title={title} />
      {children}
    </div>
  );
}

function EditHeader({ title, onBack }: { title: string; onBack?: () => void }) {
  const { t } = useI18n();
  // A dirty form must not navigate on tap, so the link becomes a button that
  // can interpose the discard confirmation first.
  const backClassName =
    "active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full";

  return (
    <header className="bg-background/95 sticky top-0 z-10 flex items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
      {onBack ? (
        <button
          type="button"
          aria-label={t.agents.title}
          data-testid="mobile-agent-edit-back"
          onClick={onBack}
          className={backClassName}
        >
          <ArrowLeftIcon aria-hidden="true" className="size-5" />
        </button>
      ) : (
        <Link
          href="/agents"
          aria-label={t.agents.title}
          data-testid="mobile-agent-edit-back"
          className={backClassName}
        >
          <ArrowLeftIcon aria-hidden="true" className="size-5" />
        </Link>
      )}
      <h1 className="min-w-0 flex-1 truncate px-1 text-base font-medium">
        {t.agents.editTitle} · {title}
      </h1>
    </header>
  );
}

function MobileAgentEditForm({ agent }: { agent: Agent }) {
  const { t } = useI18n();
  const router = useRouter();
  const updateAgent = useUpdateAgent();
  const { models } = useModels();

  const [description, setDescription] = useState(agent.description ?? "");
  const [model, setModel] = useState(agent.model ?? MODEL_DEFAULT_VALUE);
  const [soul, setSoul] = useState(agent.soul ?? "");
  const [legacyOnly, setLegacyOnly] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);

  const dirty =
    description !== (agent.description ?? "") ||
    model !== (agent.model ?? MODEL_DEFAULT_VALUE) ||
    soul !== (agent.soul ?? "");

  // Keep the agent's own model selectable while the model list is loading or
  // the configured model was removed from config.yaml.
  const modelOptions = useMemo(
    () =>
      modelOptionsWithCurrent(
        models.map((entry) => entry.name),
        agent.model,
      ),
    [models, agent.model],
  );

  const leave = () => router.push("/agents");

  function handleBack() {
    if (dirty) {
      setDiscardOpen(true);
      return;
    }
    leave();
  }

  async function handleSave() {
    try {
      await updateAgent.mutateAsync({
        name: agent.name,
        // Exactly the three editable keys: the backend merges by
        // `model_fields_set`, so anything else here would be written back.
        request: {
          description,
          model: model === MODEL_DEFAULT_VALUE ? null : model,
          soul,
        },
      });
      toast.success(t.agents.saveSuccess);
      leave();
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        toast.error(t.agents.apiDisabledError);
      } else if (err instanceof AgentLegacyLayoutError) {
        setLegacyOnly(true);
        toast.error(t.agents.legacyOnlyError);
      } else {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    }
  }

  return (
    <div className="flex min-h-full flex-col">
      <EditHeader title={agent.name} onBack={handleBack} />

      <div className="flex flex-1 flex-col gap-4 px-4 py-4">
        <p className="text-muted-foreground text-xs">
          {t.agents.editDescription}
        </p>

        {legacyOnly && (
          <p
            role="alert"
            className="border-destructive/50 text-destructive rounded-xl border p-3 text-sm"
          >
            {t.agents.legacyOnlyError}
          </p>
        )}

        <MobileAgentDescriptionField
          id="mobile-agent-edit-description"
          value={description}
          onChange={setDescription}
          disabled={legacyOnly}
        />

        <div>
          <MobileAuthLabel htmlFor="mobile-agent-edit-model">
            {t.agents.modelLabel}
          </MobileAuthLabel>
          <MobileAgentModelPicker
            id="mobile-agent-edit-model"
            value={model}
            options={modelOptions}
            onChange={setModel}
            disabled={legacyOnly}
          />
        </div>

        <MobileAgentSoulField
          id="mobile-agent-edit-soul"
          value={soul}
          onChange={setSoul}
          disabled={legacyOnly}
        />

        {/* Read-only, and the reason it is on this screen at all: the gallery
            card cannot tell `null` from `[]`, and the two mean opposite things
            once the agent runs. */}
        <div className="space-y-3 rounded-xl border p-3">
          <p className="text-muted-foreground text-xs font-medium">
            {t.agents.capabilitiesLabel}
          </p>
          <MobileAgentWhitelistRow
            testId="mobile-agent-skills"
            label={t.agents.skillsLabel}
            values={agent.skills ?? null}
            inheritText={t.agents.skillsInheritAll}
            noneText={t.agents.skillsNone}
          />
          <MobileAgentWhitelistRow
            testId="mobile-agent-tool-groups"
            label={t.agents.toolGroupsLabel}
            values={agent.tool_groups ?? null}
            inheritText={t.agents.toolGroupsInheritAll}
            noneText={t.agents.toolGroupsNone}
          />
        </div>
      </div>

      {/* The bottom edge belongs to the tab bar below `<main>` (T17), which is
          where the safe-area inset is honoured — so this bar keeps plain
          padding and sits directly above it. */}
      <div className="bg-background sticky bottom-0 z-10 flex gap-2 border-t p-4 supports-backdrop-filter:backdrop-blur">
        <Button
          variant="outline"
          className="min-h-12 flex-1 text-base"
          onClick={handleBack}
          disabled={updateAgent.isPending}
        >
          {t.common.cancel}
        </Button>
        <Button
          className="min-h-12 flex-1 text-base"
          data-testid="mobile-agent-save"
          onClick={() => void handleSave()}
          disabled={!dirty || legacyOnly || updateAgent.isPending}
        >
          <SaveIcon aria-hidden="true" className="size-4" />
          {updateAgent.isPending ? t.common.loading : t.common.save}
        </Button>
      </div>

      <Sheet open={discardOpen} onOpenChange={setDiscardOpen}>
        <SheetContent
          side="bottom"
          data-testid="mobile-agent-discard-sheet"
          className="mobile-agents-sheet gap-3 pb-[calc(env(safe-area-inset-bottom)+1rem)]"
        >
          <SheetHeader>
            <SheetTitle className="text-base">
              {t.agents.dirtyConfirmTitle}
            </SheetTitle>
            <SheetDescription>{t.agents.dirtyConfirmBody}</SheetDescription>
          </SheetHeader>
          <SheetFooter className="flex-row gap-2">
            <Button
              variant="outline"
              className="min-h-12 flex-1 text-base"
              onClick={() => setDiscardOpen(false)}
            >
              {t.agents.dirtyConfirmKeep}
            </Button>
            <Button
              variant="destructive"
              className="min-h-12 flex-1 text-base"
              data-testid="mobile-agent-discard-confirm"
              onClick={() => {
                setDiscardOpen(false);
                leave();
              }}
            >
              {t.agents.dirtyConfirmDiscard}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
