---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos. Five input modes — text-only (T2V), first-frame image, last-frame image, first+last frame, or reference images (character/style likeness). Staged workflow — every generation is confirmed with the user (plan + full prompt + cost) before any quota is spent, and 768P drafts can be upgraded to 2K. Read SKILL.md before replying — including before any clarifying question — for the mode routing, the confirmation gate, the structured-prompt methodology (MiniMax H3), and output settings.
---

# Video Generation Skill

## Overview

This skill generates short videos through a **staged, confirmed workflow**:

1. Route the request to one of five input modes.
2. Write the prompt file — MiniMax H3 uses the official **structured format**
   (`[Shot N]` timeline, three fields / six sections), loaded on demand from
   `references/`; other providers keep natural-language prose.
3. Show the user a **generation plan** (mode, output settings, the complete
   prompt, cost) and **wait for confirmation** — quota is only spent after the
   user says go.
4. Generate a 768P draft (default), present it, and offer structured next
   steps — including upgrading the take to 2K via the official regeneration
   endpoint.

Every run costs quota and is not refunded, so nothing is sent to the provider
before the user has seen and approved the exact plan.

## Core Capabilities

- Text-to-video (T2V): generate a video from a text prompt only.
- Image-to-video (I2V): use one supplied image as the first frame, the last
  frame, or both (first + last).
- Reference-to-video (Ref2VA, MiniMax H3): use images as a character/style
  likeness to imitate — up to 9 images.
- 768P → 2K **upgrade** of an existing H3 draft (regeneration endpoint; replays
  the original prompt and inputs — content corresponds, details are re-rendered).
- Cancel of still-queued tasks and a per-run sidecar task record
  (`outputs/{name}.task.json`).
- Provider/model selectable via CLI; async create → poll → download is handled
  by the script.

## Empty or vague request (clarify once, with a parameter list)

"I want to generate a video" with no picture content is not generatable — there
is nothing to write a prompt from, and every run costs quota, so do NOT invent
content. Call `ask_clarification` (type `missing_info`) ONCE using the template
below (translate to the user's language; all four parts must survive), then wait:

> A one-sentence description is enough — subject + action + setting, e.g. "a
> ginger cat stretching on a sunlit windowsill". Everything else has a default
> (MiniMax H3: duration 4–15 s, default 5 · aspect ratio 16:9, T2V only ·
> resolution 768P; minimax_v1 uses its own model defaults) —
> mention what you care about, skip the rest. You may upload images as first
> frame / last frame / first+last, or as character/style reference (up to 9).
> No professional prompt needed — I will expand your one-liner into a full
> cinematic prompt (camera, lighting, atmosphere, audio) and show it to you
> for confirmation before generating.

`options`: text only · first-frame image · last-frame image · first + last
frame · character/style reference · help me brainstorm an idea. Offer only modes
the selected provider supports (minimax_v1: first-frame only).

Once the reply contains any subject + action (even a bare "a cat video"), that
is sufficient: pick the mode, apply defaults to everything unspecified, and move
straight to Step 2 — the plan card in Step 3 states them. Do not re-ask optional
fields.

## Choosing the mode (read this first)

Route by what the user actually provided. MiniMax H3 supports five modes,
selected by `--image-role` plus the images you pass in `--reference-images`:

| The user gives you | Mode | `--image-role` | Images |
|---|---|---|---|
| Only text | **T2V** | (omit) | none |
| Text + one image to start from | **I2V (first frame)** | `first_frame` (default) | 1 |
| Text + one image to END on | **last frame** | `last_frame` | 1 |
| Text + a start image and an end image | **first + last frame** | `first_last` | 1–2 (first, then last) |
| Text + image(s) of a character/style to imitate | **reference (Ref2VA)** | `reference` | 1–9 |

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
  `first_frame`** (the most common, most intuitive reading) and note the choice
  in the plan card ("using it as the opening frame — say the word if you
  actually want a video *of* this person/style instead").
- **Self-contradictory** (asks both "start from this image" and "match this
  person's face") → only here use `ask_clarification` before writing anything.

Rationale: first-frame I2V is the mainstream use; reference is a specialist need
users usually state explicitly. Defaulting with a correction exit in the plan
card beats blocking on every upload.

### Image count and order per mode

Images map by **position** in `--reference-images` (there is no per-image
label), so order matters — confirm it with the user when it isn't obvious:

| Mode | Images | Order / rule |
|---|---|---|
| `first_frame` | 1 | the single image is the opening frame |
| `last_frame` | 1 | the single image is the closing frame |
| `first_last` | 1–2 | **first image = opening frame, second = closing frame** — never swap |
| `reference` | 1–9 | all treated as likeness references (upstream limit: 9) |

Do not pass more images than a mode uses — a 3rd image to `first_last`, or a 2nd
to `first_frame`/`last_frame`, is silently dropped, so send only what the mode
takes. For `reference`, name the images in your prompt in the same order you
pass them (the Ref2VA format labels them `<Picture 1>`, `<Picture 2>`, … by
position — see `references/prompt-format-ref.md`).

## Output settings (offer these; use defaults if the user is unsure)

Briefly surface the three output settings so the user can steer them. If the
user doesn't specify or isn't sure, use the defaults and state them on the plan
card — do NOT block or interrogate.

| Setting | Options (MiniMax H3) | Default | Notes |
|---|---|---|---|
| Aspect ratio (`--aspect-ratio`) | `16:9`, `9:16`, `1:1`, `4:3`, `21:9` | `16:9` | T2V only; for I2V the first frame fixes it (do not pass) |
| Duration (`--duration`) | 4–15 s (integer) | `5` | longer costs more; a multi-shot script needs 6 s+ |
| Resolution (`--resolution`) | `768P`, `2K` | `768P` | `2K` costs more — the default flow drafts at 768P and upgrades after confirmation (Step 6) |

Guidance:

- Casual request → draft plan = 768P · 5 s · 16:9 (T2V); state it on the plan
  card, don't ask.
- Vertical / social → suggest `9:16`; cinematic → `16:9` or `21:9`.
- Direct `2K` is the expensive path. Prefer drafting at 768P and upgrading the
  take the user likes (Step 6). If the user insists on direct 2K, the plan card
  must flag the higher platform cost.
- Explicitly given parameters are already confirmed — show them marked "(as you
  specified)" on the plan card; never re-ask them.

## Workflow

### Step 1: Understand the request

Identify: subject and action, setting, mood/style, and whether the user supplied
images (and their role). Pick the mode using the table above and the output
settings per the section above — falling back to defaults if the user is unsure.
You do not need to scan `/mnt/user-data` yourself — if an image was uploaded it
will be referenced for you.

### Step 2: Write the structured prompt file

For MiniMax H3, first load the format reference for the chosen mode, then write
the prompt file in that format:

- T2V / first frame / last frame / first+last →
  `read_file /mnt/skills/public/video-generation/references/prompt-format-base.md`
- reference mode (Ref2VA) →
  `read_file /mnt/skills/public/video-generation/references/prompt-format-ref.md`

Write the result to `/mnt/user-data/workspace/{descriptive-name}.txt` — plain
text whose content is the structured format (instruction line for keyframe
modes, then the fields). The structured format is still plain text, never a raw
JSON blob. (A `.json` file with a top-level `"prompt"` string field remains
accepted for backward compatibility — only that field is used.)

For providers other than MiniMax H3 (`minimax_v1`), keep the prose
methodology: subject + main action first, ONE main camera move, lighting,
atmosphere, ~60–100 words for a single-beat clip (audio description is H3-only).

### Step 3: Confirmation gate (always, before executing)

Call `ask_clarification` ONCE with the generation plan card, translated to the
user's language. Template (include only the cost notes that apply):

```
[Generation plan]
- Mode: {T2V | first frame | last frame | first+last | reference (N images)}
- Output: {duration}s · {resolution} · {aspect ratio}{ · ratio from the image}
- Quota: 1 video-generation credit
- Platform cost note:{ 2K billed per second at a higher rate}{ each reference
  image beyond the 5th is billed per image}
- Prompt file: /mnt/user-data/workspace/{name}.txt
- Prompt (full text):
{the complete structured prompt}

Reply "confirm" (or "go ahead") to start, or tell me what to change.
```

- `clarification_type`: `risk_confirmation` when an expensive parameter is hit
  (`2K` / `duration > 5` / more than 5 reference images) — put the platform
  cost note first and bold it; otherwise `approach_choice`.
- `options`: `["Confirm and generate", "I want to adjust"]`.
- Explicit `duration=4`: add the cost note "4s drafts cannot be upgraded to 2K
  (below the source frame minimum); use 5s+ if you may want the upgrade" —
  4 s ≈ 96 frames, under the 107-frame regeneration floor.

Confirmation protocol (prevents loops and skips):

- **Affirmative** reply (confirm / okay / go ahead / 可以 / 生成吧) → execute
  Step 4 immediately; do not re-ask anything optional.
- **Adjustment** reply → revise the prompt file and parameters, then re-show
  the FULL plan card (change summary + complete prompt) and wait again. No
  round limit; never advance without a fresh approval.
- **Resume across runs**: the gate ends the current run; the user's next
  message starts a new one. Treat the LATEST plan card in history as the only
  live one (earlier cards are superseded): if the user's latest message
  approves it — or applies an adjustment you have re-shown — execute directly,
  without re-asking and never against an older card.
- **Vague reply** ("嗯", "ok?") to a default-parameter plan counts as
  approval; to an expensive-parameter plan, re-confirm the cost once, briefly,
  then act on the answer.

### Step 4: Execute

The default draft is **768P · 5 s**. Do not pass `--resolution 2K` unless the
user insisted on direct 2K and the plan card said so.

```bash
# T2V (text only)
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --output-file /mnt/user-data/outputs/{name}.mp4

# I2V (user supplied a first frame) — no --aspect-ratio, the image fixes it
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --reference-images /mnt/user-data/uploads/{image} \
  --output-file /mnt/user-data/outputs/{name}.mp4
```

Parameters:

- `--prompt-file`: Absolute path to the prompt file (required). Structured
  `.txt` (H3), prose `.txt`, or JSON with a `"prompt"` field.
- `--reference-images`: Absolute path(s) to image(s), space-separated. Omit for
  T2V. Meaning depends on `--image-role`: the first frame (default), the last
  frame, first+last (two images), or reference images (up to 9).
- `--image-role` (MiniMax H3): `first_frame` (default), `last_frame`,
  `first_last`, or `reference`. Frame roles and `reference` are mutually
  exclusive. Omit for T2V or plain first-frame I2V.
- `--output-file`: Absolute path to the output `.mp4` (required, must NOT
  already exist).
- `--aspect-ratio`: T2V only, e.g. `16:9` (default). Ignored whenever an image
  is passed.
- `--model`: Model name, e.g. `MiniMax-H3`. This is the routing key — the
  provider that owns the model is looked up from `config.yaml`, so normally
  this is the only one you pass.
- `--provider`: Escape hatch (`minimax_h3`, `minimax_v1`) for debugging or
  for a model not declared in `config.yaml`; skips the model lookup.
- `--resolution`: `768P` (default, the draft tier) or `2K` (expensive path).
- `--duration`: Seconds, 4–15 for MiniMax H3. Default 5 (keeps the 2K upgrade
  path open — see Step 6).

[!NOTE]
Do NOT read the python file, instead just call it with the parameters.

Before dispatching, check for an unfinished duplicate: if
`outputs/{name}.task.json` exists without a terminal status (`succeeded` /
`failed` / `cancelled` / `timeout`), a task for this output may still be in
flight — report its task id and the elapsed time instead of submitting again (a
resubmission is billed again). Execution is a single foreground call: there is
no live progress while it runs; per-poll elapsed times and a stage summary
arrive in the output when it ends.

### Step 5: Present the result + iteration exits

`present_files` the video (video first, then any generated reference image).
Briefly describe the result, then ALWAYS append the iteration exits (translated
to the user's language):

```
Happy with it? I can: ① tweak the prompt ② change duration/resolution
③ swap the input image(s) ④ upgrade this take to 2K (+1 credit, same content,
refined details).
Any change re-rolls the whole clip (no fixed seed) and costs a credit.
```

Offer ④ (upgrade) ONLY for MiniMax H3 drafts whose duration allows it (4s
drafts are below the upgrade source minimum — offer ①/② instead).

### Step 6: Upgrade to 2K (optional, MiniMax H3 only)

The upgrade runs the same confirmation gate with a short plan card: source
video path + "content corresponds, details re-rendered, 768P→2K" + the
one-extra-credit note. Hard constraints for the command:

- Reuse the ORIGINAL prompt file and reference images — same paths, same order,
  same `--image-role`, same `--model` (the endpoint replays the exact original
  input; a changed input is a different generation, not an upgrade).
- `--output-file` must be a NEW path, e.g. `{name}-2k.mp4` (existing files are
  never overwritten).
- A local source video above ~45 MB is rejected (request-body cap) — shorten it
  or point `--upscale-video` at a public URL.

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

### Cancelling a queued task

`--cancel` cancels only tasks still QUEUED. Running tasks cannot be cancelled;
finished tasks are never touched (the upstream endpoint would delete their
records — the script checks state first and refuses). If the run ended by
sandbox timeout, the task may still finish upstream: its id is in
`outputs/{name}.task.json` (status `timeout`), and it can be queried later —
do not blindly regenerate.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --cancel {task_id} --model MiniMax-H3 \
  --output-file /mnt/user-data/outputs/{name}.mp4
```

(`--output-file` is optional; when given it also marks that run's `.task.json`
as cancelled.)

## Prompt methodology (summary — full specs in references/)

MiniMax H3 uses the official **structured format**; other providers keep prose.
Load the full specification in Step 2. Summary of the elements:

- **Base modes** (`references/prompt-format-base.md`): instruction line
  (i2va / fl2va / l2va only — exact official templates) + three fields:
  `integrated_multimodal_description`, `overall_soundscape`,
  `non_diegetic_music`.
- **Reference mode** (`references/prompt-format-ref.md`): six sections:
  `subject_definitions` (`<Subject N>` / `<Picture N>`, numbered by
  `--reference-images` position), `summary` (task-type prefix),
  `retention_analysis` (fixed markers `fully_preserved` / `partially_preserved`
  / `attribute_transfer` / `weak_reference`), `detailed_description`,
  `overall_soundscape`, `non_diegetic_music`.
- **Timeline**: `[Shot 1]` opens with style + composition, no timestamp; later
  shots `[Shot N] At MM:SS.mmm` with strictly increasing timestamps inside the
  duration; one dominant action per shot; a cut must add new information,
  otherwise use camera motion.
- **Camera motion**: always type + amplitude + speed as natural English.
- **Speakers**: stable IDs (S1), (S2)… reused at every vocal event; dialogue in
  `<d>[Language] exact words</d>` — original language, verbatim; voiceover adds
  "while his lips remain completely closed."
- **On-screen text**: quoted verbatim in the original language.
- **Language**: body in English; only dialogue and on-screen text stay in the
  user's language.
- **Keyframe soft-anchor** (reference mode): a reference image may be declared
  the opening/closing frame inside the prompt (see the ref format) — soft
  anchoring; pixel-exact stitching needs the frame modes.

This is an H3-specific format. Do NOT use it for `minimax_v1` — keep prose
there.

## Examples

### Example A — T2V with the gate

User: "Make a short clip of a cat stretching on a windowsill in the morning."

Step 2 — write `/mnt/user-data/workspace/cat-stretch.txt` (structured format,
full worked example in `references/prompt-format-base.md`):

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic. A medium
wide shot frames a ginger cat stretching on a sunlit wooden windowsill beside
a potted plant, arching its back and extending one paw toward the pot. Morning
golden-hour light streams through sheer curtains, casting soft volumetric rays
across its fur. The camera pushes in with small amplitude at slow speed from
the medium shot toward the cat. [Shot 2] At 00:03.000, the camera cuts to a
close-up of the paw reaching the pot's rim, dust motes drifting through the
light beam, while the stretch's lazy contentment carries over from the
previous shot.

overall_soundscape: Gentle birdsong outside, the soft rustle of curtains in a
light breeze, a faint creak of the wooden sill under the cat's weight, and a
low contented purr.

non_diegetic_music: N/A
```

Step 3 — plan card (type `approach_choice`; nothing expensive):

```
[Generation plan]
- Mode: T2V
- Output: 5s · 768P · 16:9
- Quota: 1 video-generation credit
- Prompt file: /mnt/user-data/workspace/cat-stretch.txt
- Prompt (full text):
{the prompt above}

Reply "confirm" (or "go ahead") to start, or tell me what to change.
```

After "confirm" — Step 4 (defaults apply, no extra flags needed):

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cat-stretch.txt \
  --output-file /mnt/user-data/outputs/cat-stretch.mp4
```

Step 5 — `present_files` + the iteration exits (①–④).

### Example B — I2V, user uploaded a first frame

User uploaded `portrait.jpg` and said "make her slowly turn to the camera."

Prompt file `turn.txt` starts with the i2va instruction line, then the three
fields (see the base format's per-mode strategy: anchor the image, then direct
only the motion). The plan card marks the mode "first frame"; no
`--aspect-ratio` (the image fixes it). Command:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/turn.txt \
  --reference-images /mnt/user-data/uploads/portrait.jpg \
  --output-file /mnt/user-data/outputs/turn.mp4
```

### Example C — first + last frame

User uploaded `dawn.jpg` and `dusk.jpg`: "morph the sky from dawn to dusk."

Prompt `sky.txt` uses the fl2va instruction line and a SINGLE shot describing
only the transition (both endpoints are fixed — first-frame state → narrowing
differences → last-frame state). Command (order is load-bearing: dawn first,
dusk second):

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sky.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --output-file /mnt/user-data/outputs/sky.mp4
```

### Example D — reference (Ref2VA)

User uploaded a face photo and an outfit photo: "make a video of this person
wearing this outfit, waving hello." The full six-section prompt (worked
example) is in `references/prompt-format-ref.md`. Pass the images in the order
the prompt names them — face first:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/wave.txt \
  --reference-images /mnt/user-data/uploads/face.jpg /mnt/user-data/uploads/outfit.jpg \
  --image-role reference \
  --output-file /mnt/user-data/outputs/wave.mp4
```

### Example E — upgrade the take to 2K

User (after watching `cat-stretch.mp4`): "yes — give me this one in 2K."

Short plan card → "confirm" → original prompt file and settings replayed:

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cat-stretch.txt \
  --model MiniMax-H3 \
  --upscale-video /mnt/user-data/outputs/cat-stretch.mp4 \
  --output-file /mnt/user-data/outputs/cat-stretch-2k.mp4
```

## Provider constraints

| Provider | Resolution | Duration | Aspect ratio | Image roles | Audio |
|---|---|---|---|---|---|
| `minimax_h3` | 768P / 2K | 4–15 s | T2V: `--aspect-ratio`; with any image: from image | first_frame / last_frame / first_last / reference (1–9) | Native 32 kHz stereo |
| `minimax_v1` | model default | model default | ignored | first frame only | none |

Only MiniMax H3 supports `--image-role`, `--upscale-video`, and `--cancel`;
passing them to `minimax_v1` is rejected with an error, so switch provider or
drop the flag. Frame roles (first/last) and `reference` are mutually exclusive
on H3. Reference mode takes up to 9 images (upstream limit); reference
video/audio is not supported by this skill. Unsupported params print a warning
rather than being dropped silently. Avoid named real people or trademarked
characters.

## Output handling

- Videos land in `/mnt/user-data/outputs/`; each run also writes a sidecar
  `outputs/{name}.task.json` (provider, task id, prompt file, parameters,
  status) — the source for duplicate checks and `--cancel`.
- Present the video to the user with `present_files` (video first, then any
  generated reference image if one was made).
- Give a brief description of the result and ALWAYS append the iteration exits
  (Step 5).

## Iteration (regenerate, not edit)

This skill does NOT edit an existing video — there is no video-editing capability.
"Iterating" means changing the prompt (or first frame / duration / provider) and
**generating a brand-new video from scratch**. Consequences to keep in mind:

- **Non-deterministic**: even the exact same prompt produces a different video
  each run (no fixed seed). You cannot tweak just one moment (e.g. "slow down the
  camera at 0:02") — any change re-rolls the whole clip. The ONLY same-content
  path is the 2K upgrade (Step 6), which re-renders the take at higher fidelity.
- **I2V is the most controllable iteration**: keep the same first-frame image and
  only adjust the motion/camera prose, so at least the opening frame stays stable.
  Pure T2V iteration is closer to re-rolling from scratch.
- **Use a NEW output filename each time** (e.g. `cat-v2.mp4`). The script
  **refuses to overwrite** an existing file and errors out before calling the
  provider, so a reused filename costs no quota — but it does waste a turn. Keep
  versions to compare.
- **Each run costs quota** (charged against the video-generation usage counter,
  a separate allowance from image generation; admins set weekly/monthly limits
  per user), unlike editing text — so change the prompt deliberately rather
  than re-running blindly. When the allowance is exhausted the command is
  rejected before dispatch.

## Notes

- MiniMax H3 handles English and Chinese prompts natively; the structured
  format's body is English with dialogue/on-screen text in the original
  language.
- The prompt is plain structured text — never hand the provider a raw JSON
  blob.

## Delegation boundary

Single-video requests run on the lead agent directly: the workflow is one
prompt-file write, one clarification gate, and one bash call — delegation buys
nothing, and `ask_clarification`/`present_files` are lead-agent tools (disabled
in subagents). For batch / multi-segment work (e.g. >15 s stitching), the lead
agent runs the AGGREGATE confirmation gate first (total segments + total cost),
then may delegate the per-segment executions to a subagent; the model preference
and per-run quota enforcement carry over automatically.

## Providers

The model name is the routing key: `--model` (or `VIDEO_GENERATION_MODEL`) is
reverse-looked-up in `config.yaml` `video_generation.providers[].models[]` to find
its owning provider. A model that config declares under no provider is an error
rather than a guess.

`--provider` (or `VIDEO_GENERATION_PROVIDER`) is an escape hatch that skips that
lookup. With neither a model nor a provider, resolution falls back to the first
provider in `video_generation.providers[]`, then to the credential fallback
(`MINIMAX_VIDEO_API_KEY` → `minimax_h3`, else shared `MINIMAX_API_KEY` →
`minimax_v1`).

- `minimax_h3` — MiniMax H3 via the V2 API (recommended). 768P/2K, 4-15s, native
  stereo audio, structured prompts, regeneration-based 2K upgrade, task cancel.
  T2V honors `--aspect-ratio`; for I2V the first reference image is sent as the
  first frame and the ratio follows that image. Env: `MINIMAX_VIDEO_API_KEY`
  (preferred) or the shared `MINIMAX_API_KEY`; optional `MINIMAX_API_HOST`
  (default `https://api.minimaxi.com`).
- `minimax_v1` — legacy Hailuo V1 (compatibility only). The old provider name
  `minimax` is an alias for it, so `VIDEO_GENERATION_PROVIDER=minimax` keeps the
  old behavior. Env: same credential resolution as `minimax_h3`; optional
  `MINIMAX_VIDEO_MODEL` (default `MiniMax-Hailuo-2.3`).

Params a provider does not support print a warning instead of being dropped
silently; `--image-role`, `--upscale-video`, and `--cancel` are the exceptions
and are rejected with an error.
