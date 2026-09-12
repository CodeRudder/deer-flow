"use client";

import { Badge } from "@/components/ui/badge";
import { ChannelProviderIcon } from "@/components/workspace/channels/channel-provider-icon";
import { MobileReadOnlyNotice } from "@/components/workspace/mobile/settings/settings-subpage";
import {
  useChannelConnections,
  useChannelProviders,
} from "@/core/channels/hooks";
import type { ChannelConnection, ChannelProvider } from "@/core/channels/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Channels (S6) — degraded to status only (`FEATURE_LIST.md` §1.4).
 *
 * The data is the desktop `ChannelsSettingsPage`'s (`useChannelProviders()` /
 * `useChannelConnections()`), and so is the status ladder below: disabled →
 * unconfigured → unavailable → connected / pending / revoked / not connected,
 * each mapped onto the same `t.channels.*` words. What is *not* ported is every
 * action: no connect, no reconnect, no disconnect, and no runtime-config dialog
 * (`ChannelRuntimeConfigDialog`). Binding an IM account is the desktop's job,
 * which the notice at the top states outright.
 *
 * The two status helpers are re-stated here because the desktop's are
 * module-private (`channels-settings-page.tsx` exports only the page
 * component) and this change's scope does not include that file. They are a
 * straight copy of its logic, so the two trees cannot disagree about what
 * "已连接" means.
 */
function statusLabel(
  provider: ChannelProvider,
  connection: ChannelConnection | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (!provider.enabled) {
    return t.channels.disabled;
  }
  if (!provider.configured) {
    return t.channels.unconfigured;
  }
  if (provider.unavailable_reason) {
    return t.channels.unavailableShort;
  }
  const status = connection?.status ?? provider.connection_status;
  if (status === "connected") {
    return t.channels.connected;
  }
  if (status === "pending") {
    return t.channels.pending;
  }
  if (status === "revoked") {
    return t.channels.revoked;
  }
  return t.channels.notConnected;
}

/** "account · workspace", or whichever of the two the connection carries. */
function connectionLabel(connection: ChannelConnection): string | null {
  const account = connection.external_account_name;
  const workspace = connection.workspace_name;
  if (account && workspace) {
    return `${account} · ${workspace}`;
  }
  return account ?? workspace ?? connection.external_account_id ?? null;
}

/**
 * Whether this provider is actually bound, decided from the runtime state
 * rather than from the label it happens to render — the desktop's
 * `isConnected` in `ChannelProviderItem`, kept identical so a provider whose
 * runtime is unavailable can never read as connected.
 */
function isProviderConnected(
  provider: ChannelProvider,
  connection: ChannelConnection | undefined,
): boolean {
  const runtimeAvailable = provider.configured && !provider.unavailable_reason;
  return (
    runtimeAvailable &&
    (connection?.status === "connected" ||
      provider.connection_status === "connected")
  );
}

/**
 * The provider's own description, falling back to its display name.
 *
 * Widened to `Record<string, string>` on the way in: `provider` is an open
 * string union (a new IM channel is a backend-only change), so it cannot index
 * the closed `t.channels.descriptions` table directly.
 */
function providerDescription(
  provider: ChannelProvider,
  descriptions: Record<string, string>,
): string {
  return descriptions[provider.provider] ?? provider.display_name;
}

export function MobileChannelsSection() {
  const { t } = useI18n();
  const {
    enabled,
    providers,
    isLoading: providersLoading,
    error: providersError,
  } = useChannelProviders();
  const {
    connections,
    isLoading: connectionsLoading,
    error: connectionsError,
  } = useChannelConnections();
  const isLoading = providersLoading || connectionsLoading;
  const error = providersError ?? connectionsError;
  const visibleProviders = providers.filter((provider) => provider.enabled);

  // One connection per provider, the connected one winning — the desktop's
  // rule, kept so a revoked-then-rebound account does not read as revoked.
  const connectionByProvider = new Map<string, ChannelConnection>();
  for (const connection of connections) {
    const existing = connectionByProvider.get(connection.provider);
    if (!existing || connection.status === "connected") {
      connectionByProvider.set(connection.provider, connection);
    }
  }

  return (
    <div className="space-y-4">
      <MobileReadOnlyNotice />

      {isLoading ? (
        <p className="text-muted-foreground text-base">{t.common.loading}</p>
      ) : error ? (
        <p className="text-destructive text-base">{t.channels.unavailable}</p>
      ) : !enabled || visibleProviders.length === 0 ? (
        <p className="text-muted-foreground text-base">
          {t.settings.channels.disabled}
        </p>
      ) : (
        <ul className="space-y-2">
          {visibleProviders.map((provider) => {
            const connection = connectionByProvider.get(provider.provider);
            const connected = isProviderConnected(provider, connection);
            const label = statusLabel(provider, connection, t);
            const account = connection ? connectionLabel(connection) : null;

            return (
              <li
                key={provider.provider}
                data-testid="mobile-channel-row"
                className="bg-card flex items-start gap-3 rounded-xl border p-3"
              >
                <ChannelProviderIcon
                  provider={provider.provider}
                  className="mt-0.5 size-5 shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-base font-medium">
                      {provider.display_name}
                    </span>
                    <Badge
                      variant={connected ? "default" : "outline"}
                      className={cn(!connected && "text-muted-foreground")}
                    >
                      {label}
                    </Badge>
                  </div>
                  <p className="text-muted-foreground mt-1 text-[13px]">
                    {providerDescription(provider, t.channels.descriptions)}
                    {connected && account
                      ? ` ${t.channels.connectedAs(account)}`
                      : ""}
                    {!connected && provider.unavailable_reason
                      ? ` ${provider.unavailable_reason}`
                      : ""}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
