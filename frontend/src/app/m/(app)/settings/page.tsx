import { MobileBlankScreen } from "@/components/workspace/mobile/blank-screen";
import { getI18n } from "@/core/i18n/server";

/**
 * Mobile settings root (tab-bar entry, prototype ⑦) — not built yet.
 *
 * Sits under `(app)`, so it is reachable only with a session; the guard lives
 * in the layout rather than here, same as every other signed-in screen.
 */
export default async function MobileSettingsPage() {
  const { t } = await getI18n();

  return <MobileBlankScreen title={t.settings.title} />;
}
