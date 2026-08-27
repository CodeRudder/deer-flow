# Worked mode examples (I2V / first+last / reference)

Loaded on demand when the routed mode is not plain T2V — Example A in
SKILL.md shows the full T2V flow with both gates, and Example E shows the 2K
upgrade. All examples below assume the input-table and plan-card gates have
passed and the prompt file was written in the mode's structured format (Step
2).

## B — I2V, user uploaded a first frame

User uploaded `portrait.jpg` and said "make her slowly turn to the camera."
Write `turn.txt` with the i2va instruction line and the three base fields. The
plan card marks the mode "first frame"; omit `--aspect-ratio` because the
image fixes it. Command:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/turn.txt \
  --reference-images /mnt/user-data/uploads/portrait.jpg \
  --output-file /mnt/user-data/outputs/turn.mp4
```

## B2 — last frame only

User uploaded `landing.jpg` and said "the video should END on this shot."
`landing.txt` uses the l2va instruction line and the three base fields; the
plan card marks the mode "last frame". Command:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/landing.txt \
  --reference-images /mnt/user-data/uploads/landing.jpg \
  --image-role last_frame \
  --output-file /mnt/user-data/outputs/landing.mp4
```

## C — first + last frame

User uploaded `dawn.jpg` and `dusk.jpg`: "morph the sky from dawn to dusk."
`sky.txt` uses the fl2va instruction line and a SINGLE shot describing only
the transition. Command (order is load-bearing: dawn first, dusk second):

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sky.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --output-file /mnt/user-data/outputs/sky.mp4
```

## D — reference (Ref2VA)

User uploaded a face photo and an outfit photo: "make a video of this person
wearing this outfit, waving hello." Use the six-section format in
`references/prompt-format-ref.md`; pass images in prompt order — face first:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/wave.txt \
  --reference-images /mnt/user-data/uploads/face.jpg /mnt/user-data/uploads/outfit.jpg \
  --image-role reference \
  --output-file /mnt/user-data/outputs/wave.mp4
```

## SD-1 — Seedance T2V (mini, batch/cheap tier)

User: "text-only, cheapest batch tier." Route to Seedance mini; the prompt
uses the `@`-tag/timeline format from `references/prompt-format-seedance.md`.
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
keywords (see `prompt-format-seedance.md` forbidden words).

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
