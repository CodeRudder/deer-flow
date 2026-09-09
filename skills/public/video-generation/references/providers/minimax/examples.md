# MiniMax worked mode examples (I2V / first+last / reference)

Loaded on demand when the routed provider is MiniMax H3 and the mode is not
plain T2V — Example A in SKILL.md shows the full T2V flow with both gates, and
Example E shows the 2K upgrade. All examples below assume the input-table and
plan-card gates have passed and the prompt file was written in the mode's
structured format (Step 2).

## B — I2V, user uploaded a first frame

User uploaded `portrait.jpg` and said "make her slowly turn to the camera."
Write `turn.txt` with the i2va instruction line and the three base fields. The
plan card marks the mode "first frame"; omit `--aspect-ratio` because the
image fixes it. Command:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/turn.txt \
  --reference-images /mnt/user-data/uploads/portrait.jpg \
  --model MiniMax-H3 --resolution 768P --duration 5 \
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
  --model MiniMax-H3 --resolution 768P --duration 5 \
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
  --model MiniMax-H3 --resolution 768P --duration 5 \
  --output-file /mnt/user-data/outputs/sky.mp4
```

## D — reference (Ref2VA)

User uploaded a face photo and an outfit photo: "make a video of this person
wearing this outfit, waving hello." Use the six-section format in
`prompt-format-ref.md` (same directory); pass images in prompt order — face
first:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/wave.txt \
  --reference-images /mnt/user-data/uploads/face.jpg /mnt/user-data/uploads/outfit.jpg \
  --image-role reference \
  --model MiniMax-H3 --resolution 768P --duration 5 \
  --output-file /mnt/user-data/outputs/wave.mp4
```

## D-SB — reference with storyboard design images (MiniMax H3)

User uploaded `face.jpg`, set Storyboard images = 是, Images = 有图, duration
9 s → 3 shots settled at the input table (limit check: 3 storyboard images,
0 attached originals — ≤ 5 budget, ≤ 9 cap). After the prompt file
(`wave-sb.txt`, six-section
format with per-shot `<Picture N>` anchors and the `[keyframe completion]`
prefix), generate the storyboard chain — shots 2-3 dispatched in parallel
(one foreground call) once shot 1 succeeds:

```bash
# shot 1: user material as base (image-editing)
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/uploads/face.jpg \
  --prompt {shot-1 description: framing, pose, scene; keep identity and style} \
  --output-file /mnt/user-data/outputs/wave-sb-1.png \
  --quality medium

# shots 2-3: chain from shot 1 (one foreground call: & + wait, not &&-chained)
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/outputs/wave-sb-1.png \
  --prompt {shot-2 description: change framing/action/scene only; keep identity} \
  --output-file /mnt/user-data/outputs/wave-sb-2.png \
  --quality medium &
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/outputs/wave-sb-1.png \
  --prompt {shot-3 description} \
  --output-file /mnt/user-data/outputs/wave-sb-3.png \
  --quality medium &
wait
```

Spec preflight, then the video call — storyboard images only by default
(the original's identity is already burned into the stills; `face.jpg` rides
along only when it carries info the stills don't cover), explicit ratio (no
adaptive on the storyboard path):

```bash
python /mnt/skills/public/video-generation/scripts/check_materials.py \
  --images /mnt/user-data/outputs/wave-sb-1.png /mnt/user-data/outputs/wave-sb-2.png /mnt/user-data/outputs/wave-sb-3.png \
  --out-dir /mnt/user-data/workspace
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/wave-sb.txt \
  --reference-images /mnt/user-data/outputs/wave-sb-1.png /mnt/user-data/outputs/wave-sb-2.png /mnt/user-data/outputs/wave-sb-3.png \
  --image-role reference \
  --aspect-ratio 16:9 \
  --duration 9 \
  --model MiniMax-H3 --resolution 768P \
  --output-file /mnt/user-data/outputs/wave-sb.mp4
```
