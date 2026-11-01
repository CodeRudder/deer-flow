"use client";

import { MobileReadOnlyNotice } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";
import { ModelConfigRequestError } from "@/core/models/api";
import { useManagedModels, useModels } from "@/core/models/hooks";

/**
 * Models (S5) — read-only on mobile (`FEATURE_LIST.md` §1.4).
 *
 * Two data sources, one screen, and the difference is a permission one:
 *
 * - **Administrators** get the desktop `ModelSettingsPage`'s list — the same
 *   `useManagedModels()` over `GET /api/models/config`, so a phone and the
 *   desktop dialog read the same entries. None of the desktop's add / edit /
 *   delete / thinking-probe forms (`ManagedModelList`) is ported: models are
 *   administrator configuration ("增删改留桌面").
 * - **Everyone else** would get a 403 — that endpoint is guarded by
 *   `require_admin_user` — so instead of a permission notice the screen falls
 *   back to the public `useModels()` list, i.e. the models this account can
 *   actually chat with. The prototype's annotation is explicit that the list
 *   stays *readable* ("能看已配置的模型与技能列表"); only the writes are
 *   administrator-only. Without the fallback the section would be a dead end
 *   for most accounts.
 *
 * The notice and the row's pill are what is left of the constraint either way.
 * States are kept apart on purpose: loading, a gateway that will not answer
 * (`loadError`), and an empty list are three different things, and the 403
 * branch is decided with the desktop's own `ModelConfigRequestError` class.
 *
 * Rows are normalized to one shape because the two responses differ
 * (`ManagedModel` is addressed by `name`, the public `Model` by `id`); what is
 * rendered is what both carry — display name, model id, description.
 */
export function MobileModelsSection() {
  const { t } = useI18n();
  const {
    models: managedModels,
    isLoading: managedLoading,
    error,
  } = useManagedModels();
  const adminRequired =
    error instanceof ModelConfigRequestError && error.isAdminRequired;
  // Only fetched when the managed region turned out to be forbidden.
  const { models: chatModels, isLoading: chatLoading } = useModels({
    enabled: adminRequired,
  });

  const rows = adminRequired
    ? chatModels.map((model) => ({
        key: model.id || model.name,
        title: model.display_name ?? model.name,
        model: model.model,
        description: model.description,
      }))
    : managedModels.map((model) => ({
        key: model.name,
        title: model.display_name ?? model.name,
        model: model.model,
        description: model.description,
      }));
  const loading = adminRequired ? chatLoading : managedLoading;

  return (
    <div className="space-y-4">
      <MobileReadOnlyNotice />

      {loading ? (
        <p className="text-muted-foreground text-base">{t.common.loading}</p>
      ) : error && !adminRequired ? (
        <p className="text-destructive text-sm">
          {t.settings.models.loadError.replace("{detail}", error.message)}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground text-base">
          {t.settings.models.empty}
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li
              key={row.key}
              data-testid="mobile-model-row"
              className="bg-card rounded-xl border p-3"
            >
              <p className="truncate text-base font-medium">{row.title}</p>
              <p className="text-muted-foreground truncate text-[13px]">
                {row.model}
              </p>
              {row.description ? (
                <p className="text-muted-foreground mt-1 line-clamp-2 text-[13px]">
                  {row.description}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
