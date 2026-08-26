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
