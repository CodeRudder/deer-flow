import { type ReactNode } from "react";

/**
 * A root screen that has not been built yet.
 *
 * `/agents` and `/settings` are tab-bar entries — the bar is the mobile home
 * navigation, so removing a tab would change how the app reads, but building
 * either screen is a later phase (`FEATURE_LIST.md` §1.3/§1.4). This renders
 * the screen's own title so the tab is not a dead end and the address bar
 * matches what the bar highlights, and nothing else: no fake empty state, no
 * disabled controls. A blank page is honest about being blank; an empty state
 * would imply the feature works and the user simply has no data.
 *
 * It is a plain server component — there is no state to own — and it sits under
 * `(app)`, so the tab bar renders below it and the user can leave again.
 */
export function MobileBlankScreen({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <h1 className="text-foreground text-base font-medium">{title}</h1>
      {children}
    </div>
  );
}
