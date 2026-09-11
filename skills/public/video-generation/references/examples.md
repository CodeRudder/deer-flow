# Examples

Example A shows the full T2V flow with both gates; the worked 2K-upgrade
example (Example E) lives in `references/providers/minimax/upgrade-2k.md`.
Worked commands for the other modes live in the routed provider's examples —
`references/providers/minimax/examples.md` (MiniMax) or
`references/providers/seedance/examples.md` (Seedance) — load them after the
gates when the routed mode is not plain T2V.

### Example A — explicit text-only request (pure T2V)

User: "Make a short clip of a cat stretching on a windowsill in the morning —
text only, no reference images." The explicit text-only ask opts out of the
reference-mode default. Flow: theme-prefilled input table → T2V structured
prompt (the routed provider's format file — Step 2 routing table) → plan card
(`T2V`, no materials, `5 s · draft tier · 16:9`, `Storyboard: None`,
`Initial plan`). Run:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cat-stretch.txt \
  --model {model} \
  --resolution {draft tier} \
  --duration 5 \
  --output-file /mnt/user-data/outputs/cat-stretch.mp4
```

Present with exits ①–③ plus ④ (eligible 5 s draft on the H3 upgrade path).
