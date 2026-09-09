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
  --resolution 720p --duration 5 \
  --output-file /mnt/user-data/outputs/sd-ref.mp4
```

## SD-3 — Seedance first + last frame (2.5)

User uploaded `dawn.jpg` and `dusk.jpg`: "morph the sky from dawn to dusk."
On 2.5, frame tasks lock `ratio=adaptive` (the output follows the frame
image); an explicit `--aspect-ratio` is rejected locally. This workflow uses
the default `--duration 5` rather than model-picked `-1`.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-fl.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --model doubao-seedance-2-5-260628 \
  --resolution 720p --duration 5 \
  --output-file /mnt/user-data/outputs/sd-fl.mp4
```

## SD-4 — Seedance reference with storyboard design images

User set Storyboard images = 是, Images = 无图且不找, duration 15 s → 3 shots
settled at the input table (cap check passes on 2.5 and 2.0 family alike).
No user material, so shot 1 is the text-to-image fallback
(`OPENAI_IMAGE_QUALITY=medium` inline; the input-table approval covers it —
no extra confirmation); shots 2-3 chain from shot 1 via image-editing,
dispatched in parallel once shot 1 succeeds (independent calls in the same
response — never `&&`-chained, never one-by-one):

```bash
OPENAI_IMAGE_QUALITY=medium python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-sb-1.json \
  --aspect-ratio 16:9 \
  --output-file /mnt/user-data/outputs/sd-sb-1.png

# shot 1 done → dispatch shots 2-3 in parallel (independent calls)
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/outputs/sd-sb-1.png \
  --prompt {shot-2 description: change framing/action/scene only; keep identity} \
  --output-file /mnt/user-data/outputs/sd-sb-2.png \
  --quality medium
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/outputs/sd-sb-1.png \
  --prompt {shot-3 description} \
  --output-file /mnt/user-data/outputs/sd-sb-3.png \
  --quality medium
```

Prompt file (`sd-sb.txt`) opens with the mandatory keyframe declaration
(see `prompt-format.md` storyboard section), then binds every time segment
to its own image (`[0-3秒] 段落以 @image1 的画面开始…` — per-segment binding
is mandatory, not a range note). Spec preflight, then the video call —
storyboard images only
(no original references in this case), in shot order:

```bash
python /mnt/skills/public/video-generation/scripts/check_materials.py \
  --images /mnt/user-data/outputs/sd-sb-1.png /mnt/user-data/outputs/sd-sb-2.png /mnt/user-data/outputs/sd-sb-3.png \
  --out-dir /mnt/user-data/workspace --provider seedance
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sd-sb.txt \
  --reference-images /mnt/user-data/outputs/sd-sb-1.png /mnt/user-data/outputs/sd-sb-2.png /mnt/user-data/outputs/sd-sb-3.png \
  --image-role reference \
  --aspect-ratio 16:9 \
  --duration 15 \
  --model doubao-seedance-2-5-260628 \
  --resolution 720p \
  --output-file /mnt/user-data/outputs/sd-sb.mp4
```
