# Seedance worked mode examples (T2V / multimodal reference / first+last)

Loaded on demand when the routed provider is Seedance — Example A in SKILL.md
shows the full T2V flow with both gates. All examples below assume the
input-table and plan-card gates have passed and the prompt file was written in
the @-tag/timeline format (Step 2).

## SD-1 — Seedance T2V (mini, batch/cheap tier)

User: "text-only, cheapest batch tier." Route to Seedance mini; the prompt
uses the `@`-tag/timeline format from `prompt-format.md` (same directory).
Default model is 2.5; pick mini explicitly for cost.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-t2v.txt \
  --model doubao-seedance-2-0-mini-260615 \
  --resolution 720p --duration 5 \
  --output-file /mnt/user-data/outputs/sd-t2v.mp4
```

## SD-2 — Seedance multimodal reference (image + video + audio URL)

User supplied a character image, a camera-move reference video, and a voice
reference audio — all public URLs (Seedance rejects base64 for videos and
cannot fetch local paths). Route to Seedance reference mode; the prompt binds
`@image1/@video1/@audio1` and states each material's job. Avoid edit/extend
keywords (see `prompt-format.md` forbidden words).

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-ref.txt \
  --reference-images https://cdn/char.jpg \
  --reference-videos https://cdn/move.mp4 \
  --reference-audios https://cdn/voice.mp3 \
  --image-role reference \
  --model doubao-seedance-2-5-260628 \
  --output-file /mnt/user-data/outputs/sd-ref.mp4
```

## SD-3 — Seedance first + last frame (2.5)

User uploaded `dawn.jpg` and `dusk.jpg`: "morph the sky from dawn to dusk."
On 2.5, frame tasks lock `ratio=adaptive` (the output follows the frame
image) and `duration` defaults to `-1` (model picks a length); an explicit
`--aspect-ratio` is rejected locally. Do NOT pass `--aspect-ratio` here.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-fl.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --model doubao-seedance-2-5-260628 \
  --output-file /mnt/user-data/outputs/sd-fl.mp4
```
