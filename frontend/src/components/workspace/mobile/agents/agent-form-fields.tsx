"use client";

import { Textarea } from "@/components/ui/textarea";
import {
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import { useI18n } from "@/core/i18n/hooks";

/**
 * The two text fields both agent forms share (create + edit).
 *
 * They are here rather than inlined twice because each carries a mobile rule
 * that must not drift between the two screens:
 *
 * - the text inputs are the mobile field primitives from `auth-field.tsx`
 *   (16px text, 48px tall). Despite the file name they are the tree's generic
 *   mobile field — reusing them is what keeps the iOS focus-zoom rule in one
 *   place instead of three.
 * - SOUL.md is a long, structured document, so it gets a full-height monospace
 *   editor rather than a one-line input. This is the reason the plan moves the
 *   form out of a sheet and onto a full screen at all.
 */
export function MobileAgentDescriptionField({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();

  return (
    <div>
      <MobileAuthLabel htmlFor={id}>
        {t.agents.descriptionLabel}
      </MobileAuthLabel>
      <MobileAuthField
        id={id}
        data-testid="mobile-agent-description"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
    </div>
  );
}

export function MobileAgentSoulField({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();

  return (
    <div className="flex flex-col">
      <MobileAuthLabel htmlFor={id}>{t.agents.soulLabel}</MobileAuthLabel>
      {/* `font-mono` because SOUL.md is markdown; `text-base` (16px) keeps iOS
          from zooming the page when the editor takes focus — the shared
          `Textarea` only sets `md:text-sm`, which never applies on a phone but
          would win at tablet widths if it were not restated. */}
      <Textarea
        id={id}
        data-testid="mobile-agent-soul"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="min-h-56 resize-none font-mono text-base leading-relaxed"
      />
    </div>
  );
}
