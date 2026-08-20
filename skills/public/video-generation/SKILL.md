---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos. Five input modes — text-only (T2V), first-frame image, last-frame image, first+last frame, or reference images (character/style likeness, ≤5). Read SKILL.md before replying — including before any clarifying question — for the mode, prompt methodology, output settings, and the required question format when a request carries no picture content.
---

# Video Generation Skill

## Overview

This skill generates short videos from a **natural-language prompt**. The prompt
is a single piece of well-organized prose — NOT a JSON structure. If the user
supplies an image, it is used as the first frame (image-to-video); otherwise the
video is generated from text alone (text-to-video).

## Core Capabilities

- Text-to-video (T2V): generate a video from a text prompt only.
- Image-to-video (I2V): use one supplied image as the first frame, the last
  frame, or both (first + last).
- Reference-to-video (Ref2VA, MiniMax H3): use images as a character/style
  likeness to imitate (not a frame of the video) — at most 5 images.
- Provider/model selectable via CLI; async create → poll → download is handled
  by the script.

## Empty or vague request (clarify once, with a parameter list)

"I want to generate a video" with no picture content is not generatable — there
is nothing to write a prompt from, and every run costs quota, so do NOT invent
content. Call `ask_clarification` (type `missing_info`) ONCE using the template
below (translate to the user's language; all four parts must survive), then wait:

> A one-sentence description is enough — subject + action + setting, e.g. "a
> ginger cat stretching on a sunlit windowsill". Everything else has a default
> (MiniMax H3: duration 4–15 s, default 4 · aspect ratio 16:9, T2V only ·
> resolution 768P; gemini / minimax_v1 use their own model defaults) —
> mention what you care about, skip the rest. You may upload images as first
> frame / last frame / first+last, or as character/style reference (≤5).
> No professional prompt needed — I will expand your one-liner into a full
> cinematic prompt (camera, lighting, atmosphere, audio) before generating.

`options`: text only · first-frame image · last-frame image · first + last
frame · character/style reference · help me brainstorm an idea. Offer only modes
the selected provider supports (gemini: multi-image reference; minimax_v1:
first-frame only).

Once the reply contains any subject + action (even a bare "a cat video"), that
is sufficient: pick the mode, apply defaults to everything unspecified, state
them in one line, and generate. Do not re-ask optional fields.

## Choosing the mode (read this first)

Route by what the user actually provided. MiniMax H3 supports five modes,
selected by `--image-role` plus the images you pass in `--reference-images`:

| The user gives you | Mode | `--image-role` | Images |
|---|---|---|---|
| Only text | **T2V** | (omit) | none |
| Text + one image to start from | **I2V (first frame)** | `first_frame` (default) | 1 |
| Text + one image to END on | **last frame** | `last_frame` | 1 |
| Text + a start image and an end image | **first + last frame** | `first_last` | 1–2 (first, then last) |
| Text + image(s) of a character/style to imitate | **reference (Ref2VA)** | `reference` | 1–5 |

**Frame vs. reference — the key distinction (they are mutually exclusive):**

- A **frame** image (first/last) is an *actual frame of the video* — it literally
  appears on screen at 0:00 (first) or at the end (last). The model fills in the
  motion between frames. Think: "start the video from this photo."
- A **reference** image is *not any frame of the video* — it never appears
  as-is. It only tells the model "the character/style looks like this," and the
  model transfers that identity/look into a freshly generated video. Think:
  "make a video of this person, who looks like this photo."

Because one pins a concrete frame on the timeline and the other transfers
features, H3 does not allow mixing frame roles with reference roles in one
request. Pick the mode from intent: is the image a moment *in* the video (frame)
or a likeness to *imitate* (reference)?

T2V is a first-class native capability, not a fallback. For a text-only request,
generating a reference image first is **opt-in** (the user must ask): it costs an
extra image-generation call and locks the video to that frame. When in doubt for
a text-only request, do T2V.

### When the image role is ambiguous

With a single image and a vague request ("make a video from this image"), the
frame-vs-reference intent is genuinely ambiguous. Resolve it by signal — do NOT
interrogate on every image:

- **Clear frame signal** ("make this photo move", "start from this", "end on
  this") → go straight to the frame mode, no question.
- **Clear reference signal** ("generate a video of this person", "in this
  style", "a character who looks like this") → go straight to `reference`, no
  question.
- **Weak / no signal** (just "make a video from this image") → **default to
  `first_frame`** (the most common, most intuitive reading) and add a one-line
  correction exit in your reply, e.g. "I'll use it as the opening frame — tell me
  if you actually want a video *of* this person/style instead."
- **Self-contradictory** (asks both "start from this image" and "match this
  person's face") → only here use `ask_clarification` before running.

Rationale: first-frame I2V is the mainstream use; reference is a specialist need
users usually state explicitly. Defaulting with a correction exit beats blocking
on every upload.

### Image count and order per mode

Images map by **position** in `--reference-images` (there is no per-image
label), so order matters — confirm it with the user when it isn't obvious:

| Mode | Images | Order / rule |
|---|---|---|
| `first_frame` | 1 | the single image is the opening frame |
| `last_frame` | 1 | the single image is the closing frame |
| `first_last` | 1–2 | **first image = opening frame, second = closing frame** — never swap |
| `reference` | 1–5 | all treated as likeness references; capped at 5 (images from the 6th on are billed) |

Do not pass more images than a mode uses — a 3rd image to `first_last`, or a 2nd
to `first_frame`/`last_frame`, is silently dropped, so send only what the mode
takes. For `reference`, mention the images in your prompt in the same order you
pass them (e.g. "the face from the first reference, the outfit from the
second").

## Output settings (offer these; use defaults if the user is unsure)

Briefly surface the three output settings so the user can steer them. If the
user doesn't specify or isn't sure, use the defaults and just mention them — do
NOT block or interrogate.

| Setting | Options (MiniMax H3) | Default | Notes |
|---|---|---|---|
| Aspect ratio (`--aspect-ratio`) | `16:9`, `9:16`, `1:1`, `4:3`, `21:9` | `16:9` | T2V only; for I2V the first frame fixes it (do not pass) |
| Duration (`--duration`) | 4–15 s (integer) | `4` | longer costs more; a multi-shot script needs 6 s+ |
| Resolution (`--resolution`) | `768P`, `2K` | `768P` | `2K` costs more |

Guidance:
- Casual request → proceed with defaults and state them, e.g. "I'll make a
  4-second 768P 16:9 clip — tell me if you want it longer, vertical, or in 2K."
- Vertical / social → suggest `9:16`; cinematic → `16:9` or `21:9`.
- Call out the higher cost when the user asks for `2K` or a long duration.

## Workflow

### Step 1: Understand the request

Identify: subject and action, setting, mood/style, and whether the user supplied
a first-frame image. Pick the mode using the table above, and confirm the output
settings (aspect ratio / duration / resolution) per the section above — falling
back to defaults if the user is unsure. You do not need to scan `/mnt/user-data`
yourself — if an image was uploaded it will be referenced for you.

### Step 2: Write the prompt file (natural-language prose)

Write the prompt to `/mnt/user-data/workspace/{descriptive-name}.txt` as a
single prose paragraph (see the methodology below). Plain `.txt` is the primary
format. (A `.json` file with a top-level `"prompt"` string field is also
accepted for backward compatibility — only that field is used; every other key
is ignored. Do not rely on JSON structure to carry camera/audio/etc.)

### Step 3: Execute

```bash
# T2V (text only)
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --output-file /mnt/user-data/outputs/{name}.mp4 \
  --aspect-ratio 16:9 --resolution 768P --duration 4

# I2V (user supplied a first frame)
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --reference-images /mnt/user-data/uploads/{image} \
  --output-file /mnt/user-data/outputs/{name}.mp4 \
  --resolution 768P --duration 4
```

Parameters:

- `--prompt-file`: Absolute path to the prompt file (required). Prose `.txt`, or
  JSON with a `"prompt"` field.
- `--reference-images`: Absolute path(s) to image(s), space-separated. Omit for
  T2V. Meaning depends on `--image-role`: the first frame (default), the last
  frame, first+last (two images), or reference images (at most 5).
- `--image-role` (MiniMax H3): `first_frame` (default), `last_frame`,
  `first_last`, or `reference`. Frame roles and `reference` are mutually
  exclusive. Omit for T2V or plain first-frame I2V.
- `--output-file`: Absolute path to the output `.mp4` (required).
- `--aspect-ratio`: T2V only, e.g. `16:9`. Ignored whenever an image is passed
  (the image fixes the aspect ratio).
- `--model`: Model name, e.g. `MiniMax-H3`. This is the routing key — the provider
  that owns the model is looked up from `config.yaml`, so normally this is the only
  one you pass.
- `--provider`: Escape hatch (`minimax_h3`, `gemini`, `minimax_v1`) for debugging or
  for a model not declared in `config.yaml`; skips the model lookup.
- `--resolution`: `768P` (cheaper, default) or `2K` for MiniMax H3.
- `--duration`: Seconds, 4–15 for MiniMax H3. Default 4.

[!NOTE]
Do NOT read the python file, instead just call it with the parameters.

## Prompt methodology

The provider's `prompt` receives natural-language prose. Structure your thinking
with the 6-part skeleton, then write it out as flowing prose (not labels, not
JSON):

1. **Subject + main action** (put first — the opening words carry the most
   weight): who/what and the single dominant motion.
2. **Setting**: concrete, interactable environment elements.
3. **Camera**: use industry motion terms — `dolly in/out`, `tracking shot`,
   `crane up`, `orbit`, `rack focus`, `static shot`, `handheld`, `push in`.
   Pick ONE main move; multiple camera moves fight each other in a short clip.
4. **Lighting + look**: the single biggest quality lever — `golden hour`,
   `volumetric light`, `chiaroscuro`, `tungsten practical light`; plus grade/
   depth of field.
5. **Atmosphere**: mood/pacing in a phrase.
6. **Audio** (MiniMax H3 only — it generates native 32 kHz stereo): describe
   ambience/SFX in prose, and you may specify channel (e.g. "a low purr in the
   right channel"). Other providers ignore audio description.

Guidelines:
- Target ~60–100 words for a single-beat clip. The first 20–30 words matter most.
- **Duration vs. beats**: the default 4 s fits only 1–2 action beats. Only write
  a multi-shot / timecoded script (`[0:00–0:03] WIDE — …`) when you set
  `--duration` high enough (6 s+). Do not cram multiple actions into 4 s.
- **No negative-prompt field exists.** Express "don't" constraints as positive
  prose: `single subject throughout`, `keep the framing consistent`,
  `no scene change`.
- MiniMax H3 also honors inline camera markers in Chinese prompts, e.g.
  `[运镜：推近]`.

### I2V — three extra rules

When the user supplies a first frame:

1. **Anchor it**: start with `Use the supplied image as the exact opening frame.`
2. **Write only the increment**: describe what *happens next* (motion, light
   change), NOT what the frame already shows — the subject's appearance and the
   scene are already in the image.
3. **Reverse-write consistency**: since there is no negative field, pin identity
   with positive prose — `keep the face, framing, and lighting consistent with
   the first frame`, `single subject`, `no scene change`.

Note: for I2V, do NOT pass `--aspect-ratio` — the first frame fixes it.

## Examples

### Example A — T2V (text only)

User: "Make a short clip of a cat stretching on a windowsill in the morning."

Step 2 — write `/mnt/user-data/workspace/cat-stretch.txt`:

```
A ginger cat stretches lazily on a sunlit wooden windowsill, arching its back
and extending one paw toward a potted plant. Morning golden-hour light streams
through sheer curtains, casting soft volumetric rays across its fur. Slow dolly
in from a medium shot to a close-up as it yawns. Warm cinematic color grade,
shallow depth of field, cozy domestic atmosphere. Ambient audio: gentle birdsong
outside, the soft rustle of curtains, and a low contented purr in the right channel.
```

Step 3:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cat-stretch.txt \
  --output-file /mnt/user-data/outputs/cat-stretch.mp4 \
  --aspect-ratio 16:9 --resolution 768P --duration 4
```

### Example B — I2V (user uploaded a first frame)

User uploaded `portrait.jpg` and said "make her slowly turn to the camera."

Step 2 — write `/mnt/user-data/workspace/turn.txt`:

```
Use the supplied image as the exact opening frame. The woman slowly turns her
head toward the camera and offers a faint smile as a gentle breeze lifts a few
strands of hair. Soft cinematic light, subtle push in over four seconds. Keep the
face, wardrobe, framing, and lighting exactly consistent with the first frame;
single subject throughout; no scene change. Ambient audio: a soft room tone with
faint wind.
```

Step 3 (no `--aspect-ratio` — the frame fixes it):

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/turn.txt \
  --reference-images /mnt/user-data/uploads/portrait.jpg \
  --output-file /mnt/user-data/outputs/turn.mp4 \
  --resolution 768P --duration 4
```

### Example C — first + last frame (MiniMax H3)

User uploaded `dawn.jpg` and `dusk.jpg`: "morph the sky from dawn to dusk."

Prompt (`sky.txt`) writes only the transition, since both endpoints are fixed:

```
The sky transforms smoothly from dawn to dusk over the same skyline — warm
orange sunrise light gradually deepening into purple and indigo as stars emerge.
Clouds drift slowly across the frame; a static locked-off shot throughout.
```

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sky.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --output-file /mnt/user-data/outputs/sky.mp4 \
  --resolution 768P --duration 6
```

### Example D — reference (Ref2VA, MiniMax H3)

User uploaded a **face photo** (`face.jpg`) and a separate **outfit photo**
(`outfit.jpg`) and said "make a video of this person wearing this outfit, waving
hello." The images are a *likeness to imitate*, not frames of the video. There
is no per-image label, so reference each one by its **position** in
`--reference-images` using plain language — pass them in the order your prompt
names them:

```
A young man with the face from the first reference image, wearing the outfit
from the second reference image, waves hello at the camera with a warm smile in
a bright modern studio. Natural soft lighting, medium shot, gentle handheld
feel. Keep his facial features and the outfit consistent with the references.
```

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/wave.txt \
  --reference-images /mnt/user-data/uploads/face.jpg /mnt/user-data/uploads/outfit.jpg \
  --image-role reference \
  --output-file /mnt/user-data/outputs/wave.mp4 \
  --resolution 768P --duration 4
```

The order is load-bearing: `face.jpg` is passed first because the prompt says
"the first reference image" for the face. Confirm the order with the user when
it isn't obvious.

## Provider constraints

| Provider | Resolution | Duration | Aspect ratio | Image roles | Audio |
|---|---|---|---|---|---|
| `minimax_h3` | 768P / 2K | 4–15 s | T2V: `--aspect-ratio`; with any image: from image | first_frame / last_frame / first_last / reference (1–5) | Native 32 kHz stereo |
| `gemini` | model default | model default | model default (ignored) | reference asset(s) only | none |
| `minimax_v1` | model default | model default | ignored | first frame only | none |

Only MiniMax H3 supports `--image-role`; passing it to `gemini` or `minimax_v1`
is rejected with an error, so switch provider or drop the flag. Frame roles
(first/last) and `reference` are mutually exclusive on H3. Reference mode takes
at most 5 images (images from the 6th on are billed); reference video/audio
is not supported by this skill. Unsupported params print a warning rather than
being dropped silently. Avoid named real people or trademarked characters.

## Output handling

- Videos land in `/mnt/user-data/outputs/`.
- Present the video to the user with `present_files` (video first, then any
  generated reference image if one was made).
- Give a brief description of the result and offer to iterate.

## Iteration (regenerate, not edit)

This skill does NOT edit an existing video — there is no video-editing capability.
"Iterating" means changing the prompt (or first frame / duration / provider) and
**generating a brand-new video from scratch**. Consequences to keep in mind:

- **Non-deterministic**: even the exact same prompt produces a different video
  each run (no fixed seed). You cannot tweak just one moment (e.g. "slow down the
  camera at 0:02") — any change re-rolls the whole clip.
- **I2V is the most controllable iteration**: keep the same first-frame image and
  only adjust the motion/camera prose, so at least the opening frame stays stable.
  Pure T2V iteration is closer to re-rolling from scratch.
- **Use a NEW output filename each time** (e.g. `cat-v2.mp4`). The script
  **refuses to overwrite** an existing file and errors out before calling the
  provider, so a reused filename costs no quota — but it does waste a turn. Keep
  versions to compare.
- **Each run costs quota** (shares the image-generation usage counter), unlike
  editing text — so change the prompt deliberately rather than re-running blindly.

## Notes

- Prefer clear English prose for broad compatibility; MiniMax H3 also handles
  Chinese prompts natively (including `[运镜：…]` markers).
- The prompt is prose, not data — never hand the provider a raw JSON blob.

## Providers

The model name is the routing key: `--model` (or `VIDEO_GENERATION_MODEL`) is
reverse-looked-up in `config.yaml` `video_generation.providers[].models[]` to find
its owning provider. A model that config declares under no provider is an error
rather than a guess.

`--provider` (or `VIDEO_GENERATION_PROVIDER`) is an escape hatch that skips that
lookup. With neither a model nor a provider, resolution falls back to the first
provider in `video_generation.providers[]`, then to the credential fallback
(`GEMINI_API_KEY` → `gemini`, else `MINIMAX_VIDEO_API_KEY` → `minimax_h3`, else
shared `MINIMAX_API_KEY` → `minimax_v1`).

- `minimax_h3` — MiniMax H3 via the V2 API (recommended). 768P/2K, 4-15s, native
  stereo audio. T2V honors `--aspect-ratio`; for I2V the first reference image is
  sent as the first frame and the ratio follows that image. Env:
  `MINIMAX_VIDEO_API_KEY` (preferred) or the shared `MINIMAX_API_KEY`; optional
  `MINIMAX_API_HOST` (default `https://api.minimaxi.com`).
- `gemini` — Google Veo (`x-goog-api-key` auth); reference images are passed as
  asset images. Env: `GEMINI_API_KEY`.
- `minimax_v1` — legacy Hailuo V1 (compatibility only). The old provider name
  `minimax` is an alias for it, so `VIDEO_GENERATION_PROVIDER=minimax` keeps the
  old behavior. Env: same credential resolution as `minimax_h3`; optional
  `MINIMAX_VIDEO_MODEL` (default `MiniMax-Hailuo-2.3`).

Params a provider does not support print a warning instead of being dropped
silently; `--image-role` is the exception and is rejected with an error.
