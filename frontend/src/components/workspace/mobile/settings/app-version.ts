import projectPackage from "../../../../../package.json";

/**
 * The version the settings screen reports (prototype ⑦ shows `v2.1.0` on the
 * 关于 row).
 *
 * Read from the frontend's own `package.json` — the same number the release
 * process bumps — rather than typed into the component, so the screen cannot
 * drift from the build. The value is a static import: it is baked in at build
 * time, not fetched, so it renders on the first frame.
 */
export const APP_VERSION = `v${projectPackage.version}`;
