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

`minimax_h3_sglang` (self-hosted sglang) is deliberately **absent from the
credential fallback**: it has no credential to probe, and it is a single-GPU
serial endpoint that must never be picked up implicitly. Reach it by declaring
it in `video_generation.providers` and selecting its model, or via the
`--provider` escape hatch.

## Providers

Per-provider credentials, endpoints, and capability notes live in the
provider folders: `references/providers/minimax/spec.md`,
`references/providers/minimax_h3_sglang/spec.md`, and
`references/providers/seedance/spec.md`.

## Parameter compatibility

`--query` is read-only and works on every provider. Params a provider does not
support print a warning instead of being dropped silently; `--image-role`,
`--reference-videos`, `--reference-audios`, `--upscale-video`, and `--cancel`
are the exceptions and are rejected with an error.

Which parameters each provider honors — plus value domains, per-model
defaults, reference caps, aspect-ratio enums, and supported image roles —
comes from the adapter manifest:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py --describe-provider
```

