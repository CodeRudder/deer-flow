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
fallback (`MINIMAX_VIDEO_API_KEY` → `minimax_h3`, else shared
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

## Parameter compatibility

`--query` is read-only and works on every provider. Params a provider does not
support print a warning instead of being dropped silently; `--image-role`,
`--upscale-video`, and `--cancel` are the exceptions and are rejected with an
error (MiniMax H3 only).

| Param | `minimax_h3` | `minimax_v1` |
|---|---|---|
| `--resolution` / `--duration` | 768P/2K · 4–15 s (defaults 768P · 5) | ignored (model-side defaults) |
| `--aspect-ratio` | per-mode semantics — see SKILL.md Output settings | ignored |
| `--reference-images` | per `--image-role` | first frame only (one image) |
| `--image-role` | `first_frame` / `last_frame` / `first_last` / `reference` | rejected |
| `--upscale-video` | 2K regeneration | rejected |
| `--cancel` | queued tasks only | rejected |
| `--query` | supported | supported |
