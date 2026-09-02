# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.1.15) — Fundamental AI building blocks
- **TanStack Query** (`@tanstack/react-query` ^5.90.17) — Server state management
- **UI**: Shadcn UI, MagicUI, React Bits, and Vercel AI SDK elements (generated from registries — see Code Style)

## Commands

| Command          | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `pnpm dev`       | Dev server with Turbopack (http://localhost:3000) |
| `pnpm build`     | Production build                                  |
| `pnpm check`     | Lint + type check (run before committing)         |
| `pnpm lint`      | ESLint only                                       |
| `pnpm lint:fix`  | ESLint with auto-fix                              |
| `pnpm format`    | Prettier check (`pnpm format:write` to apply)     |
| `pnpm test`      | Run unit tests with Rstest                        |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)          |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)            |
| `pnpm start`     | Start production server                           |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Rstest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code) and **todos**.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes include `/` (landing), `/workspace/chats/[thread_id]` (chat), `/workspace/agents/[agent_name]` and `/workspace/agents/new` (custom agents), `/blog/…`, the `(auth)/{login,setup,auth/callback}` flow, `/[lang]/docs/…`, and `/api/…` route handlers (e.g. `/api/memory`).
- **`components/`** — React components:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
  - `docs/` — Docs / MDX rendering components
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `suggestions/`, `tasks/`, `todos/`, `tools/`, `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`content/`** — MDX content (blog posts, docs) rendered by the app
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming
- **`typings/`** — Ambient TypeScript declarations
- Root files: `env.js` (env validation), `mdx-components.ts` (MDX component map)

### Data Flow

1. User input → thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos)
3. TanStack Query manages server state; localStorage stores user settings
4. Components subscribe to thread state and render updates

Model selection stores two independent per-thread context fields:
`model_name` selects the chat model, while `vision_model_name` selects the
independent image-understanding model exposed by `/api/models` as
`vision_models`. The model picker groups chat models and vision models; when no
vision model is pinned, the backend falls back to the first configured
`vision.models[]` entry.

Image and video generation keep their workspace pickers, and both selectors are
flat single-level menus showing model names directly (no provider submenu). The
video selector enriches each row with the user-facing point rate (`X 积分/秒`,
range across resolutions) sourced from the billing rules via
`GET /api/video-generation/providers`, with per-resolution detail and supported
duration in the row tooltip. Only
`image_generation_model` / `video_generation_model` are sent as thread context —
the provider is resolved from the model name by the backend. `/providers` still
returns provider groups with `configured`, which the selectors use for labels
and disabled states without transmitting the provider. Image editing is
backend-only in this iteration and is triggered through the `image-editing`
skill, so there is no separate frontend selector yet.

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **Admin session tracing** (`components/workspace/admin/session-trace-panel.tsx`, `core/admin/`) derives user-overview versus exact-trace mode from the user/thread/run filters. It starts with no selected user and loads the latest all-user Run page; the separate user selector enables a 30-day overview and scopes the list only after explicit selection. User overview queries are enabled only when a user is selected and both exact IDs are empty; Run events load only after the read-only detail drawer opens, whose tool-call overview consumes the backend-provided `tool_summary` instead of reparsing event payloads in the browser.
- **Admin user usage ranking** uses one `/api/admin/usage/users` response to render separate Token, model-request, image-generation, and video-generation Top 20 vertical charts. Each chart has its own ordering, scale, horizontal scrollbar, and pointer-following tooltip; the retained `/api/admin/usage/sessions` client remains available but is no longer queried by the panel.
- **Admin quota control** is range-first and database-owned: `/api/admin/quotas/scopes` lets administrators manually create model groups or the single image-generation / video-generation range, manage `models[].model` exact/prefix membership, and set default per-user weekly/monthly policies plus an optional per-dimension daily cap (`日额度拦截` switch + `每日上限` input, sent as `daily: {enforced, limit}` on the matching metric; the user adjustment drawer mirrors it with `日上限` + `日额度拦截`, showing today's usage when configured). Video ranges always use points, expose an administrator-edited CNY-per-second rate JSON (`1 point = 1 CNY`), and show used/reserved points. A user-side quota indicator (`components/workspace/quota-indicator.tsx` + `core/quotas/`) renders the same aggregation in the chat header for the signed-in user via `GET /api/quotas/me`: a compact pill showing only a wallet icon and a 额度(quota) label, no dropdown chevron (warning/exceeded tone derived from the response's top-level status), with per-dimension details in a dropdown rendered strictly in the backend-returned order (the service sorts Python-side: model groups general → premium, then image, then video generation; premium tier = any `match_rules` exact/prefix entry containing claude/gpt) as flat compact rows `name used[/limit] unit` (`/limit` renders only for enforced dimensions; video reserved points render inline; no progress bar) with a right-aligned muted weekly/monthly badge per row whose `title` carries the full period range (rows use fixed-tone lucide dimension icons — blue bot for general model groups, amber sparkles for premium (scope_code containing claude/gpt), green image, violet video — no brand-color logos); no pre-generation estimate or settlement line in this release. There is no `config.yaml` quota switch/default. Quota-range cards assign distinct low-saturation badge colors by stable list position; card backgrounds and semantic status colors remain unchanged. User edits are current-period overrides with an explicit restore-default action; model Token usage is read-only observation and never an enforcement field. The user table consumes the model-group usage summaries already returned for the current database page and dynamically generates one column per enabled model group, with numeric request/Token usage aligned by scope ID, plus fixed image/video generation-usage columns. The table marks temporary overrides; editing remains in the adjustment drawer without N+1 requests. Each drawer card presents current requests, Token observation (model scopes), and the effective limit as prominent read-only metrics above the override form.
- **Admin user management** is the fourth dashboard tab and defaults to active accounts. `components/workspace/admin/user-management-panel.tsx` renders status summaries, server-side search/filter/pagination, explicit approve/disable/enable actions, failed approval-email retry, and the normal-local-user-only “编辑” dialog (currently email only); administrator profiles are read-only. Account and approval-email wire values are centralized as `as const` enum-like objects in `core/auth/user-status.ts`, with derived union types and Zod value tuples shared by auth and admin code. Its client contracts live in `core/admin/`; successful mutations invalidate both the list and summary. Registration HTTP 202 stays on the auth page and renders the pending-approval state instead of refreshing the session or navigating to the workspace.
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`

### Interaction Ownership

- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission.

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*` routes.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Core Concepts](https://js.langchain.com/docs/concepts)
- [TanStack Query Documentation](https://tanstack.com/query/latest)
- [Next.js App Router](https://nextjs.org/docs/app)

## Contributing

When adding features:

1. Follow the established `src/` structure
2. Add TypeScript types and proper error handling
3. Write unit tests under `tests/unit/` (`pnpm test`) and E2E tests under `tests/e2e/` (`pnpm test:e2e`)
4. Run `pnpm check` before committing
5. Update this `AGENTS.md` when architecture, commands, or conventions change
