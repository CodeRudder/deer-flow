"use client";

import { BellIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings } from "@/core/settings";

/**
 * Notification (S3), retained on mobile.
 *
 * Every branch is the desktop `NotificationSettingsPage`'s: the same
 * `useNotification()` (permission, support probe, the test notification) and
 * the same `useLocalSettings()` switch, which is the flag the chat page reads
 * before firing a completion notification. The desktop hangs its switch off the
 * section *description*; here it is the first row, because on a phone the
 * control is the point of the screen and the explanation belongs under it.
 *
 * The switch stays disabled until the browser has granted permission — the
 * desktop's rule, kept: the stored flag is meaningless while `Notification.
 * permission` is `default` or `denied`, and a switch that flips without effect
 * is worse than one that is visibly waiting.
 */
export function MobileNotificationSection() {
  const { t } = useI18n();
  const { permission, isSupported, requestPermission, showNotification } =
    useNotification();
  const [settings, setSettings] = useLocalSettings();

  const handleRequestPermission = async () => {
    await requestPermission();
  };

  const handleTestNotification = () => {
    showNotification(t.settings.notification.testTitle, {
      body: t.settings.notification.testBody,
    });
  };

  const handleEnableNotification = (enabled: boolean) => {
    setSettings("notification", { enabled });
  };

  if (!isSupported) {
    return (
      <p className="text-muted-foreground text-base">
        {t.settings.notification.notSupported}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex min-h-14 items-center justify-between gap-3 rounded-xl border p-3">
        <span className="text-base font-medium">
          {t.settings.notification.title}
        </span>
        <Switch
          aria-label={t.settings.notification.title}
          disabled={permission !== "granted"}
          checked={permission === "granted" && settings.notification.enabled}
          onCheckedChange={handleEnableNotification}
        />
      </div>

      <p className="text-muted-foreground text-sm">
        {t.settings.notification.description}
      </p>

      {permission === "default" && (
        <Button
          onClick={() => void handleRequestPermission()}
          className="min-h-12 w-full gap-2 text-base"
        >
          <BellIcon aria-hidden="true" className="size-5" />
          {t.settings.notification.requestPermission}
        </Button>
      )}

      {permission === "denied" && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/50">
          {t.settings.notification.deniedHint}
        </p>
      )}

      {permission === "granted" && settings.notification.enabled && (
        <Button
          onClick={handleTestNotification}
          variant="outline"
          className="min-h-12 w-full gap-2 text-base"
        >
          <BellIcon aria-hidden="true" className="size-5" />
          {t.settings.notification.testButton}
        </Button>
      )}
    </div>
  );
}
