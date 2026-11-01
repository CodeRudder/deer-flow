# 2K upgrade (MiniMax H3 regeneration)

Loaded from SKILL.md when the user chooses the ④ upgrade exit (Step 5): read
this file BEFORE rendering the confirmation card, and execute only after the
user's explicit confirmation.

## Confirmation gate

The upgrade runs a short confirmation gate showing the source video path and
"content corresponds, details re-rendered, 768P→2K". Do not include model,
quota, cost, or the full prompt in the user-facing card. Execute only after an
explicit confirmation; cancellation returns to SKILL.md Step 5. A prompt,
material, or setting change starts a new generation plan rather than changing
the upgrade.

## Hard constraints for the command

- Reuse the ORIGINAL prompt file and reference images — same paths, order,
  `--image-role`, and `--model` (the endpoint replays the exact original
  input; a changed input is a new generation, not an upgrade).
- `--output-file` must be a NEW path, e.g. `{name}-2k.mp4` (never overwritten).
- A local source above ~45 MB is rejected (request-body cap). No object
  storage is configured, so no public URL can be produced for the draft —
  the only exit is drafting a shorter take: a new generation that must
  pass the full plan-card gate.

## Command

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --reference-images {exactly the original run's images, if any} \
  --image-role {exactly the original run's role, if any} \
  --model {exactly the original run's model} \
  --upscale-video /mnt/user-data/outputs/{name}.mp4 \
  --output-file /mnt/user-data/outputs/{name}-2k.mp4
```

Do NOT pass `--duration` / `--aspect-ratio` / `--resolution` together with
`--upscale-video` — the upgrade runs at a fixed 2K and follows the source.

## Worked example (Example E)

User (after watching `cat-stretch.mp4`): "yes — give me this one in 2K."

Brief confirmation card (source video path + content-corresponds note) →
"confirm" → original prompt file and settings replayed:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cat-stretch.txt \
  --model MiniMax-H3 \
  --upscale-video /mnt/user-data/outputs/cat-stretch.mp4 \
  --output-file /mnt/user-data/outputs/cat-stretch-2k.mp4
```
