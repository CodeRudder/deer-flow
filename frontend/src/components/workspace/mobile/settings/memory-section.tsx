"use client";

import { PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
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
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useI18n } from "@/core/i18n/hooks";
import {
  useClearMemory,
  useCreateMemoryFact,
  useDeleteMemoryFact,
  useMemory,
  useUpdateMemoryFact,
} from "@/core/memory/hooks";
import type { MemoryFact, UserMemory } from "@/core/memory/types";
import { formatTimeAgo } from "@/core/utils/datetime";

/**
 * Memory (S4), retained on mobile — reading *and* writing, unlike S5/S8.
 *
 * The data layer is the desktop `MemorySettingsPage`'s: the same
 * `useMemory()` + `useCreateMemoryFact()` / `useUpdateMemoryFact()` /
 * `useDeleteMemoryFact()` / `useClearMemory()` (shared query key, so a fact
 * added here shows up in the desktop dialog), the same client-side validation
 * before the request, the same toast wording.
 *
 * What is not ported:
 * - **export / import.** Both are file-shaped (a downloaded JSON blob, a
 *   `<input type="file">` with a 40-line structural validator) and belong with
 *   the Web Share/export work, not with a phone settings screen.
 * - **the summary markdown renderer.** The desktop builds a markdown document
 *   and renders tables (`buildMemorySectionGroups` + `SafeStreamdown`, both
 *   module-private); a 390px table is the wrong shape, so each summary is
 *   rendered as its own card with the same headings and the same
 *   `formatTimeAgo` timestamp.
 *
 * Facts are still fully editable: add, edit and delete all go through the
 * desktop's endpoints, which is what "保留（读写个人记忆）" asks for.
 */

interface FactFormState {
  content: string;
  category: string;
  confidence: string;
}

const EMPTY_FACT_FORM: FactFormState = {
  content: "",
  category: "",
  confidence: "1",
};

/**
 * The desktop's confidence → level-key mapping, restated (its helper is
 * module-private). The bands are the desktop's — 0.85 / 0.65 — so the two trees
 * cannot describe the same fact differently.
 */
function confidenceLevelKey(
  confidence: unknown,
): "veryHigh" | "high" | "normal" | "unknown" {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) {
    return "unknown";
  }
  if (confidence >= 0.85) return "veryHigh";
  if (confidence >= 0.65) return "high";
  return "normal";
}

interface MemorySummarySection {
  title: string;
  summary: string;
  updatedAt: string;
}

function buildSummaryGroups(
  memory: UserMemory,
  t: ReturnType<typeof useI18n>["t"],
): { title: string; sections: MemorySummarySection[] }[] {
  return [
    {
      title: t.settings.memory.markdown.userContext,
      sections: [
        {
          title: t.settings.memory.markdown.work,
          summary: memory.user.workContext.summary,
          updatedAt: memory.user.workContext.updatedAt,
        },
        {
          title: t.settings.memory.markdown.personal,
          summary: memory.user.personalContext.summary,
          updatedAt: memory.user.personalContext.updatedAt,
        },
        {
          title: t.settings.memory.markdown.topOfMind,
          summary: memory.user.topOfMind.summary,
          updatedAt: memory.user.topOfMind.updatedAt,
        },
      ],
    },
    {
      title: t.settings.memory.markdown.historyBackground,
      sections: [
        {
          title: t.settings.memory.markdown.recentMonths,
          summary: memory.history.recentMonths.summary,
          updatedAt: memory.history.recentMonths.updatedAt,
        },
        {
          title: t.settings.memory.markdown.earlierContext,
          summary: memory.history.earlierContext.summary,
          updatedAt: memory.history.earlierContext.updatedAt,
        },
        {
          title: t.settings.memory.markdown.longTermBackground,
          summary: memory.history.longTermBackground.summary,
          updatedAt: memory.history.longTermBackground.updatedAt,
        },
      ],
    },
  ];
}

export function MobileMemorySection() {
  const { t } = useI18n();
  const { memory, isLoading, error } = useMemory();
  const clearMemory = useClearMemory();
  const createFact = useCreateMemoryFact();
  const updateFact = useUpdateMemoryFact();
  const deleteFact = useDeleteMemoryFact();

  const [form, setForm] = useState<FactFormState>(EMPTY_FACT_FORM);
  const [editingFact, setEditingFact] = useState<MemoryFact | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [factToDelete, setFactToDelete] = useState<MemoryFact | null>(null);
  const [clearOpen, setClearOpen] = useState(false);

  if (isLoading) {
    return (
      <p className="text-muted-foreground text-base">{t.common.loading}</p>
    );
  }

  if (error) {
    return <p className="text-destructive text-sm">{error.message}</p>;
  }

  if (!memory) {
    return (
      <p className="text-muted-foreground text-base">
        {t.settings.memory.memoryFullyEmpty}
      </p>
    );
  }

  const summaryGroups = buildSummaryGroups(memory, t);

  function openCreate() {
    setEditingFact(null);
    setForm(EMPTY_FACT_FORM);
    setEditorOpen(true);
  }

  function openEdit(fact: MemoryFact) {
    setEditingFact(fact);
    setForm({
      content: fact.content,
      category: fact.category,
      confidence: String(fact.confidence),
    });
    setEditorOpen(true);
  }

  async function handleSaveFact() {
    const content = form.content.trim();
    if (!content) {
      toast.error(t.settings.memory.factValidationContent);
      return;
    }
    const confidence = Number(form.confidence);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      toast.error(t.settings.memory.factValidationConfidence);
      return;
    }
    const input = {
      content,
      category: form.category.trim() || "context",
      confidence,
    };

    try {
      if (editingFact) {
        await updateFact.mutateAsync({ factId: editingFact.id, input });
        toast.success(t.settings.memory.editFactSuccess);
      } else {
        await createFact.mutateAsync(input);
        toast.success(t.settings.memory.addFactSuccess);
      }
      setEditorOpen(false);
      setEditingFact(null);
      setForm(EMPTY_FACT_FORM);
    } catch (mutationError) {
      toast.error(
        mutationError instanceof Error
          ? mutationError.message
          : String(mutationError),
      );
    }
  }

  async function handleDeleteFact() {
    if (!factToDelete) {
      return;
    }
    try {
      await deleteFact.mutateAsync(factToDelete.id);
      toast.success(t.settings.memory.factDeleteSuccess);
      setFactToDelete(null);
    } catch (mutationError) {
      toast.error(
        mutationError instanceof Error
          ? mutationError.message
          : String(mutationError),
      );
    }
  }

  async function handleClearMemory() {
    try {
      await clearMemory.mutateAsync();
      toast.success(t.settings.memory.clearAllSuccess);
      setClearOpen(false);
    } catch (mutationError) {
      toast.error(
        mutationError instanceof Error
          ? mutationError.message
          : String(mutationError),
      );
    }
  }

  const saving = createFact.isPending || updateFact.isPending;

  return (
    <div className="space-y-8">
      {summaryGroups.map((group) => (
        <SettingsSection key={group.title} title={group.title}>
          <div className="space-y-2">
            {group.sections.map((section) => (
              <div
                key={section.title}
                className="bg-card rounded-xl border p-3"
              >
                <p className="text-sm font-medium">{section.title}</p>
                <p className="text-muted-foreground mt-1 text-sm whitespace-pre-wrap">
                  {section.summary.trim() || t.settings.memory.markdown.empty}
                </p>
                {section.updatedAt ? (
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t.settings.memory.markdown.updatedAt}:{" "}
                    {formatTimeAgo(section.updatedAt)}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </SettingsSection>
      ))}

      <SettingsSection title={t.settings.memory.markdown.facts}>
        <div className="space-y-3">
          {memory.facts.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              {t.settings.memory.noFacts}
            </p>
          ) : (
            <ul className="space-y-2">
              {memory.facts.map((fact) => (
                <li
                  key={fact.id}
                  data-testid="mobile-memory-fact"
                  className="bg-card rounded-xl border p-3"
                >
                  <p className="text-base whitespace-pre-wrap">
                    {fact.content}
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <Badge variant="outline">{fact.category}</Badge>
                    <span className="text-muted-foreground text-xs">
                      {
                        t.settings.memory.markdown.table.confidenceLevel[
                          confidenceLevelKey(fact.confidence)
                        ]
                      }
                    </span>
                    <span className="ml-auto flex items-center">
                      <button
                        type="button"
                        aria-label={`${t.common.edit} ${fact.content}`}
                        data-testid="mobile-memory-fact-edit"
                        className="active:bg-accent flex size-11 items-center justify-center rounded-full"
                        onClick={() => openEdit(fact)}
                      >
                        <PencilIcon aria-hidden="true" className="size-5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`${t.common.delete} ${fact.content}`}
                        data-testid="mobile-memory-fact-delete"
                        className="active:bg-accent text-destructive flex size-11 items-center justify-center rounded-full"
                        onClick={() => setFactToDelete(fact)}
                      >
                        <Trash2Icon aria-hidden="true" className="size-5" />
                      </button>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {editorOpen ? (
            <div className="space-y-3 rounded-xl border p-3">
              <div>
                <MobileAuthLabel htmlFor="mobile-memory-content">
                  {t.settings.memory.factContentLabel}
                </MobileAuthLabel>
                <textarea
                  id="mobile-memory-content"
                  data-testid="mobile-memory-content"
                  value={form.content}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      content: event.target.value,
                    }))
                  }
                  placeholder={t.settings.memory.factContentPlaceholder}
                  className="border-input bg-card text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 min-h-24 w-full rounded-xl border p-3 text-base outline-none focus-visible:ring-[3px]"
                />
              </div>
              <div>
                <MobileAuthLabel htmlFor="mobile-memory-category">
                  {t.settings.memory.factCategoryLabel}
                </MobileAuthLabel>
                <MobileAuthField
                  id="mobile-memory-category"
                  value={form.category}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      category: event.target.value,
                    }))
                  }
                  placeholder={t.settings.memory.factCategoryPlaceholder}
                />
              </div>
              <div>
                <MobileAuthLabel htmlFor="mobile-memory-confidence">
                  {t.settings.memory.factConfidenceLabel}
                </MobileAuthLabel>
                <MobileAuthField
                  id="mobile-memory-confidence"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  inputMode="decimal"
                  value={form.confidence}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      confidence: event.target.value,
                    }))
                  }
                />
                <p className="text-muted-foreground mt-1 text-xs">
                  {t.settings.memory.factConfidenceHint}
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  className="min-h-12 flex-1 text-base"
                  onClick={() => {
                    setEditorOpen(false);
                    setEditingFact(null);
                  }}
                  disabled={saving}
                >
                  {t.common.cancel}
                </Button>
                <Button
                  className="min-h-12 flex-1 text-base"
                  data-testid="mobile-memory-save"
                  onClick={() => void handleSaveFact()}
                  disabled={saving}
                >
                  {t.settings.memory.factSave}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="outline"
              className="min-h-12 w-full gap-2 text-base"
              data-testid="mobile-memory-add"
              onClick={openCreate}
            >
              <PlusIcon aria-hidden="true" className="size-5" />
              {t.settings.memory.addFact}
            </Button>
          )}

          <Button
            variant="destructive"
            className="min-h-12 w-full text-base"
            data-testid="mobile-memory-clear"
            onClick={() => setClearOpen(true)}
          >
            {t.settings.memory.clearAll}
          </Button>
        </div>
      </SettingsSection>

      <AlertDialog
        open={factToDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setFactToDelete(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t.settings.memory.factDeleteConfirmTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t.settings.memory.factDeleteConfirmDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              data-testid="mobile-memory-fact-delete-confirm"
              onClick={() => void handleDeleteFact()}
            >
              {t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t.settings.memory.clearAllConfirmTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t.settings.memory.clearAllConfirmDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              data-testid="mobile-memory-clear-confirm"
              onClick={() => void handleClearMemory()}
            >
              {t.settings.memory.clearAll}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
