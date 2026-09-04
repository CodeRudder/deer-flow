"use client";

import { BotIcon, SaveIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { type Agent, useAgent, useUpdateAgent } from "@/core/agents";
import {
  AgentLegacyLayoutError,
  AgentsApiDisabledError,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";

/** Radix Select forbids empty item values, so "follow default" (model: null) uses a sentinel. */
export const MODEL_DEFAULT_VALUE = "__default__";

interface AgentEditSheetProps {
  agent: Agent;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * View + edit an existing custom agent: SOUL.md, description, and model are
 * editable; tool_groups / skills stay read-only with their three-state
 * semantics (null = inherit all, [] = none, list = whitelist) rendered
 * explicitly. The detail query re-fetches on every open so stale gallery data
 * cannot clobber what an in-chat `update_agent` just wrote.
 */
export function AgentEditSheet({
  agent,
  open,
  onOpenChange,
}: AgentEditSheetProps) {
  const { t } = useI18n();
  const [dirty, setDirty] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleOpenChange = (next: boolean) => {
    if (!next && dirty) {
      setConfirmOpen(true);
      return;
    }
    setDirty(false);
    onOpenChange(next);
  };

  return (
    <>
      <Sheet open={open} onOpenChange={handleOpenChange}>
        <SheetContent className="flex size-full flex-col gap-0 overflow-hidden sm:max-w-xl">
          <SheetHeader className="shrink-0">
            <SheetTitle className="flex items-center gap-2">
              <BotIcon className="text-primary h-4 w-4" />
              {t.agents.editTitle} · {agent.name}
            </SheetTitle>
            <SheetDescription>{t.agents.editDescription}</SheetDescription>
          </SheetHeader>
          {/* Conditional render so every open starts from a fresh form state. */}
          {open && (
            <AgentEditForm
              key={agent.name}
              agent={agent}
              onDirtyChange={setDirty}
              onSaved={() => onOpenChange(false)}
            />
          )}
        </SheetContent>
      </Sheet>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.dirtyConfirmTitle}</DialogTitle>
            <DialogDescription>{t.agents.dirtyConfirmBody}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t.agents.dirtyConfirmKeep}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmOpen(false);
                setDirty(false);
                onOpenChange(false);
              }}
            >
              {t.agents.dirtyConfirmDiscard}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface AgentEditFormProps {
  agent: Agent;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
}

function AgentEditForm({ agent, onDirtyChange, onSaved }: AgentEditFormProps) {
  const { t } = useI18n();
  const { agent: freshAgent, error: detailError } = useAgent(agent.name);
  const updateAgent = useUpdateAgent();
  const { models } = useModels();

  // The form starts from the gallery data; once the detail query lands (and
  // the user has not started typing yet) adopt it as the new baseline so the
  // editor reflects what an in-chat update_agent may just have written.
  const base = freshAgent ?? agent;
  const [description, setDescription] = useState(base.description ?? "");
  const [model, setModel] = useState(base.model ?? MODEL_DEFAULT_VALUE);
  const [soul, setSoul] = useState(base.soul ?? "");
  const [legacyOnly, setLegacyOnly] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  const dirty =
    description !== (base.description ?? "") ||
    model !== (base.model ?? MODEL_DEFAULT_VALUE) ||
    soul !== (base.soul ?? "");

  useEffect(() => {
    if (detailError) {
      toast.error(t.agents.loadDetailFailed);
    }
  }, [detailError, t.agents.loadDetailFailed]);

  useEffect(() => {
    if (freshAgent && !dirty && !hydrated) {
      setDescription(freshAgent.description ?? "");
      setModel(freshAgent.model ?? MODEL_DEFAULT_VALUE);
      setSoul(freshAgent.soul ?? "");
      setHydrated(true);
    }
  }, [freshAgent, dirty, hydrated]);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // Keep the current override selectable even while the model list is loading
  // or the configured model was removed from config.yaml.
  const modelOptions = useMemo(() => {
    const names = models.map((m) => m.name);
    if (base.model && !names.includes(base.model)) {
      names.unshift(base.model);
    }
    return names;
  }, [models, base.model]);

  async function handleSave() {
    try {
      await updateAgent.mutateAsync({
        name: agent.name,
        request: {
          description,
          model: model === MODEL_DEFAULT_VALUE ? null : model,
          soul,
        },
      });
      toast.success(t.agents.saveSuccess);
      onSaved();
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
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
        <div className="space-y-2">
          <label
            className="text-sm font-medium"
            htmlFor="agent-edit-description"
          >
            {t.agents.descriptionLabel}
          </label>
          <Input
            id="agent-edit-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={legacyOnly}
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="agent-edit-model">
            {t.agents.modelLabel}
          </label>
          <Select value={model} onValueChange={setModel} disabled={legacyOnly}>
            <SelectTrigger id="agent-edit-model" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={MODEL_DEFAULT_VALUE}>
                {t.agents.modelDefault}
              </SelectItem>
              {modelOptions.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex min-h-56 flex-1 flex-col gap-2">
          <label className="text-sm font-medium" htmlFor="agent-edit-soul">
            {t.agents.soulLabel}
          </label>
          <Textarea
            id="agent-edit-soul"
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
            disabled={legacyOnly}
            className="min-h-56 flex-1 resize-none font-mono text-xs"
          />
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <p className="text-muted-foreground text-xs font-medium">
            {t.agents.capabilitiesLabel}
          </p>
          <WhitelistRow
            label={t.agents.skillsLabel}
            values={base.skills ?? null}
            inheritText={t.agents.skillsInheritAll}
            noneText={t.agents.skillsNone}
          />
          <WhitelistRow
            label={t.agents.toolGroupsLabel}
            values={base.tool_groups ?? null}
            inheritText={t.agents.toolGroupsInheritAll}
            noneText={t.agents.toolGroupsNone}
          />
        </div>
      </div>

      <SheetFooter className="shrink-0 border-t">
        <Button
          variant="outline"
          onClick={() => onSaved()}
          disabled={updateAgent.isPending}
        >
          {t.common.cancel}
        </Button>
        <Button
          onClick={() => void handleSave()}
          disabled={!dirty || legacyOnly || updateAgent.isPending}
        >
          <SaveIcon className="mr-1.5 h-4 w-4" />
          {updateAgent.isPending ? t.common.loading : t.common.save}
        </Button>
      </SheetFooter>
    </>
  );
}

/**
 * Renders one capability whitelist with its three states spelled out: no key
 * in config.yaml ("inherit all"), an empty list ("none"), or the listed
 * entries. This visibility is the point of the read-only section — the card
 * gallery cannot distinguish null from [].
 */
function WhitelistRow({
  label,
  values,
  inheritText,
  noneText,
}: {
  label: string;
  values: string[] | null;
  inheritText: string;
  noneText: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-sm font-medium">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {values === null ? (
          <Badge variant="secondary">{inheritText}</Badge>
        ) : values.length === 0 ? (
          <Badge variant="outline">{noneText}</Badge>
        ) : (
          values.map((value) => (
            <Badge
              key={value}
              variant="outline"
              className="max-w-full"
              title={value}
            >
              <span className="truncate">{value}</span>
            </Badge>
          ))
        )}
      </div>
    </div>
  );
}
