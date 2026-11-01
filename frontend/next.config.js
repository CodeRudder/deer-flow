/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

function getInternalServiceURL(envKey, fallbackURL) {
  const configured = process.env[envKey]?.trim();
  return configured && configured.length > 0
    ? configured.replace(/\/+$/, "")
    : fallbackURL;
}
import nextra from "nextra";

const withNextra = nextra({});

/** @type {import("next").NextConfig} */
const config = {
  output:
    process.env.NEXT_CONFIG_BUILD_OUTPUT === "standalone"
      ? "standalone"
      : undefined,
  i18n: {
    locales: ["en", "zh"],
    defaultLocale: "en",
  },
  devIndicators: false,
  // Do NOT re-enable response compression (Next's default) while the LangGraph
  // stream is proxied through this server's `/api/langgraph` rewrite.
  //
  // With compression on, the SSE response of `…/runs/{id}/stream` goes out with
  // `Content-Encoding: gzip` and is buffered by the zlib stream instead of being
  // flushed per event: the browser then receives nothing until the buffer fills
  // or the run ends (measured: 0 events for 38s, then the whole run at once).
  // The user-visible damage is exactly the streaming UI: a page reload mid-run
  // sits on the "thinking" placeholder with no reasoning text and no tool/step
  // rows, and a live transcript jumps in bursts. nginx (:2026) does not
  // compress the stream, which is why the two entry points behave differently.
  // Verified: `curl -D - -H 'Accept-Encoding: gzip' …/runs/{id}/stream` reports
  // `Content-Encoding: gzip` on the Next server and none on nginx; with this
  // flag off the header is gone and the same browser session renders the
  // replayed run immediately after a reload.
  compress: false,
  // Next 16 blocks dev-resource requests whose Origin host is not allowlisted.
  // That includes the HMR websocket, and when it fails the app never hydrates:
  // the page renders fine but every click is inert and nothing is logged, so it
  // reads as "login does nothing". A subnet wildcard keeps LAN testing working
  // when DHCP hands out a different address (e.g. from a phone). Dev-only.
  // `127.0.0.1` is not covered by Next's built-in `localhost` entries, so a page
  // opened at http://127.0.0.1:3000 fails to hydrate exactly like the LAN case
  // above. Verified: no React fiber on the submit button, so the form falls back
  // to a native GET and login silently does nothing.
  allowedDevOrigins: ["192.168.2.*", "127.0.0.1"],
  async rewrites() {
    const rewrites = [];
    const gatewayURL = getInternalServiceURL(
      "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL",
      "http://127.0.0.1:8001",
    );

    if (!process.env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
      rewrites.push({
        source: "/api/langgraph",
        destination: `${gatewayURL}/api`,
      });
      rewrites.push({
        source: "/api/langgraph/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    if (!process.env.NEXT_PUBLIC_BACKEND_BASE_URL) {
      rewrites.push({
        source: "/api/agents",
        destination: `${gatewayURL}/api/agents`,
      });
      rewrites.push({
        source: "/api/agents/:path*",
        destination: `${gatewayURL}/api/agents/:path*`,
      });
      rewrites.push({
        source: "/api/skills",
        destination: `${gatewayURL}/api/skills`,
      });
      rewrites.push({
        source: "/api/skills/:path*",
        destination: `${gatewayURL}/api/skills/:path*`,
      });

      // Catch-all for remaining gateway API routes (models, threads, memory,
      // mcp, artifacts, uploads, suggestions, runs, etc.) that don't have
      // their own NEXT_PUBLIC_* env var toggle.
      //
      // NOTE: this must come AFTER the /api/langgraph rewrite above so that
      // LangGraph-compatible routes keep their public prefix while Gateway
      // receives its native /api/* paths.
      rewrites.push({
        source: "/api/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    return rewrites;
  },
};

export default withNextra(config);
