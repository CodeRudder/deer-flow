// Selective gzip wrapper around `next start`.
//
// Why: next.config.js sets `compress: false` because Next's built-in
// compression buffers SSE (`Content-Encoding: gzip` on the LangGraph stream
// breaks per-event flushing; see the comment there). But that also sends every
// JS chunk and HTML page uncompressed, which hurts slow links (Tailscale
// relay ~50 kB/s).
//
// This server fronts Next's request handler with `compression()` middleware
// and skips compressible responses ONLY for the paths that carry SSE:
//   - /api/langgraph/** (the Next rewrite target users hit through :3000)
//   - text/event-stream responses (belt and braces)
//
// Cache-Control is left to Next's defaults, which already serve
// `/_next/static/**` with `public, max-age=31536000, immutable` - measured
// live on this deployment. HTML stays no-store (correct: it is a redirect /
// auth-gated shell).

import http from "node:http";
import compression from "compression";
import next from "next";

const port = Number(process.env.PORT || 3000);
const hostname = process.env.HOSTNAME || "0.0.0.0";

// Any request whose path can carry an SSE stream must bypass compression.
function isStreamPath(pathname) {
  return (
    pathname === "/api/langgraph" ||
    pathname.startsWith("/api/langgraph/") ||
    pathname.startsWith("/api/threads/") && pathname.includes("/runs/")
  );
}

const app = next({ dev: false, hostname, port });
const handle = app.getRequestHandler();

await app.prepare();

const server = http.createServer((req, res) => {
  const pathname = (req.url || "").split("?")[0];

  if (isStreamPath(pathname)) {
    handle(req, res);
    return;
  }

  // compression() only kicks in when the client sends Accept-Encoding: gzip
  // and the response has a compressible content-type; it also sets
  // `Vary: Accept-Encoding` and respects the response's Cache-Control.
  compression({ filter: (req, res) => {
    if ((res.getHeader("Content-Type") || "").includes("text/event-stream")) {
      return false;
    }
    // Default filter behaviour for everything else.
    return compression.filter(req, res);
  }})(req, res, () => {
    handle(req, res);
  });
});

server.listen(port, hostname, () => {
  console.log(`[next-gzip] ready on ${hostname}:${port} (SSE paths bypass compression)`);
});
