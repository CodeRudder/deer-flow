# Provider runtime: routing, credentials, compatibility

Loaded on demand — read this file when provider selection fails, credentials
are unclear, or a parameter is rejected or ignored.

## Routing

The model name is the routing key: `--model` (or `VIDEO_GENERATION_MODEL`) is
reverse-looked-up in `config.yaml` `video_generation.providers[].models[]` to
find its owning provider. A model that config declares under no provider is an
error rather than a guess.

`--provider` (or `VIDEO_GENERATION_PROVIDER`) is an escape hatch that skips
that lookup. With neither a model nor a provider, resolution falls back to the
first provider in `video_generation.providers[]`, then to the credential
fallback (`MINIMAX_VIDEO_API_KEY` → `minimax_h3`, else
`SEEDANCE_VIDEO_API_KEY`/`ARK_API_KEY` → `seedance`, else shared
`MINIMAX_API_KEY` → `minimax_v1`).

## Providers

- `minimax_h3` — MiniMax H3 via the V2 API (recommended). 768P/2K, 4–15 s,
  native stereo audio, structured prompts, regeneration-based 2K upgrade, task
  cancel. Env: `MINIMAX_VIDEO_API_KEY` (preferred) or the shared
  `MINIMAX_API_KEY`; optional `MINIMAX_API_HOST` (default
  `https://api.minimaxi.com`).
- `minimax_v1` — legacy Hailuo V1 (compatibility only). The old provider name
  `minimax` is an alias for it, so `VIDEO_GENERATION_PROVIDER=minimax` keeps
  the old behavior. Env: same credential resolution as `minimax_h3`; optional
  `MINIMAX_VIDEO_MODEL` (default `MiniMax-Hailuo-2.3`).
- `seedance` — Volcano Ark doubao-seedance-2.x family, one adapter for four
  models. `doubao-seedance-2-5-260628` (30 s, 50 multimodal references,
  480p/720p/1080p), `doubao-seedance-2-0-260128` (only model with 4k; also
  1080p), `…-fast-…` / `…-mini-…` (480p/720p, cheaper tiers; mini ≈ half the
  standard price). Reference video/audio are URL-only (the API rejects base64
  and cannot fetch local paths). Seedance 2.5 locks frame/edit/extend tasks to
  ratio=adaptive; only video-edit also locks duration=-1 (API Ref) — enforced
  locally with a clear error. Env: `SEEDANCE_VIDEO_API_KEY` (preferred) or
  `ARK_API_KEY`; optional `SEEDANCE_API_BASE_URL` (default
  `https://ark.cn-beijing.volces.com/api/v3`) and `SEEDANCE_VIDEO_MODEL`.

## Parameter compatibility

`--query` is read-only and works on every provider. Params a provider does not
support print a warning instead of being dropped silently; `--image-role`,
`--reference-videos`, `--reference-audios`, `--upscale-video`, and `--cancel`
are the exceptions and are rejected with an error.

| Param | `minimax_h3` | `minimax_v1` | `seedance` |
|---|---|---|---|
| `--resolution` / `--duration` | 768P/2K · 4–15 s (defaults 768P · 5) | ignored (model-side defaults) | per model: 480p/720p/1080p (2.5 & 2.0 standard); 4k (2.0 standard only) · 4–15 s (4–30 s on 2.5, or -1 auto) |
| `--aspect-ratio` | per-mode semantics — see SKILL.md Output settings | ignored | same enum; frame modes on 2.5 are forced adaptive |
| `--reference-images` | per `--image-role` | first frame only (one image) | per `--image-role`; reference mode up to 30 (2.5) / 9 (2.0 family) |
| `--reference-videos` / `--reference-audios` | rejected | rejected | reference mode only; public URLs only; ≤10/≤10 (2.5), ≤3/≤3 (2.0 family) |
| `--image-role` | `first_frame` / `last_frame` / `first_last` / `reference` | rejected | same roles |
| `--upscale-video` | 2K regeneration | rejected | rejected |
| `--cancel` | queued tasks only | rejected | queued tasks only |
| `--query` | supported | supported | supported |
