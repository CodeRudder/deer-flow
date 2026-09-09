---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos. Three input modes — text-only (T2V), first/last frame (首尾帧, precise start/end locking), or reference images (character/style likeness). Staged workflow — always show a prefilled input information table, then confirm a fixed user-facing generation plan before execution; drafts can be upgraded to 2K (MiniMax H3). Read SKILL.md before replying — including before any clarifying question — for the input table, mode routing, confirmation gate, structured-prompt methodology (MiniMax H3) or @-tag/timeline methodology (Seedance — also accepts reference videos/audios), and output settings.
---

# Video Generation Skill

## Overview

This skill generates short videos through a **staged, confirmed workflow**:

1. Parse every new video request into a fixed, prefilled **input information
   table** and wait for the user to continue or edit it. Only a theme is
   required — it may be vague or abstract; the skill decomposes it into
   subject + action and completes everything else. The dedicated **Storyboard images row** (next to Storyboard) carries
   the opt-in intent for design stills (one per shot — see Step 2.5); its
   limits are settled at this table.
2. Route the confirmed inputs to one of three user-facing input modes
   (reference mode by default; 首尾帧 maps internally to the first / last /
   first+last frame entries), perform only the necessary material checks, and
   collect missing
   reference material BEFORE prompt writing when the user opted in.
3. Write the prompt file — route by provider: MiniMax H3 uses the official
   **structured format** (`[Shot N]` timeline, three fields / six sections) from
   `references/providers/minimax/prompt-format-base.md` (T2V/frame modes) or `prompt-format-ref.md`
   (reference mode); Seedance uses the **@-tag + timeline format**
   (`@image1`/`@video1`/`@audio1` material tags, time-axis segments) from
   `references/providers/seedance/prompt-format.md`; `minimax_v1` keeps natural-language prose. On the storyboard path the prompt declares the
   per-shot design images before they exist.
4. Generate the **storyboard design images** (opt-in path only — see
   Step 2.5), then show
   a fixed **generation plan** containing the mode, materials (actual
   storyboard files; originals attached only per the default-only-storyboard
   rule), output, prompt path with user-language refined description,
   storyboard list, change summary, and capability note; then
   wait for confirmation.
5. Generate a draft-tier video (default resolution is the provider's draft
   tier — `--describe-provider`; MiniMax H3 drafts are upgradable
   to 2K via the official regeneration endpoint, Seedance has
   no upgrade path), present it, and offer structured next steps.

Nothing is sent to the video or image provider before the user has approved
it at the applicable gate: the video plan requires the plan-card approval;
storyboard design images (when the user opted in at the input table) require
the input-table approval that stated the image-quota cost — both approvals
must be on record before the corresponding provider call.

Capabilities: T2V; I2V (first / last / first+last frame); reference transfer (Ref2VA — per-provider reference caps via `--describe-provider`, including reference videos/audios on Seedance); storyboard design images (opt-in per-shot keyframe stills, any reference-capable provider); 2K upgrade (MiniMax H3); extended durations on Seedance 2.5; queued-task cancel; read-only task query; per-run sidecar (`outputs/{name}.task.json`). Async create → poll → download is handled by the script.

## Input information table (always show before routing)

For **every new video request**, first parse everything the user already gave
you, then call `ask_clarification` with the fixed **Markdown table** below,
translated to the user's language. This gate is mandatory even when the theme
is already present — and the table is shown even when the theme is missing.
Prefill supplied values; do not make the user repeat them.

| Field | Prefilled value |
|---|---|
| Theme* | {user content — may be vague or abstract / needs input} |
| Mode | T2V / first+last frame (首尾帧) / reference [✓] — mark the inferred mode (default `reference`) |
| Subject | {decomposed from the theme / user override} |
| Action | {decomposed from the theme / user override} |
| Setting | {user content / auto-complete} |
| Style | {user content / auto-complete} |
| Camera | {user content / auto-complete} |
| Mood | {user content / auto-complete} |
| Sound | {user content / auto-complete} |
| Storyboard | {user content / plan as needed — text-only shot planning: how many shots, what happens in each; no image-generation intent} |
| Storyboard images | 否 [✓] (default) / 是 — one design still per shot, consumes image-generation quota (cost noted in the option text; situational mention only for a multi-shot story with no materials) |
| Duration | {user content / default 5 s; Seedance 2.5 frame tasks default to model-picked length (`-1`)} |
| Aspect ratio | 16:9 / 9:16 / 1:1 / 21:9 / adaptive [✓] (default — T2V and the storyboard path render 16:9; reference stays adaptive) / other: {user value} |
| Resolution | 480P / 720P / 768P / 1080P / 2K — [✓] marks the routed provider's draft tier (value domain via `--describe-provider`) |
| Images | 有图 ({filenames and intent — user-provided, agent may supplement}) / 无图帮我找 [✓] (collect for me: search first; image generation only after approval) / 无图且不找 (skip collection) — preselect per the user's wording; unstated → 无图帮我找 |

Only a **theme** is required — it may be vague or abstract; the skill
decomposes it into subject + action while writing the prompt. Tell the user:
**anything left unfilled — including reference images — will be automatically
completed and collected** (rules in Choosing the mode; the Images row's
three-state collection intent overrides this default when stated). Only
treat files the user supplied in this conversation as user-provided.
Reply "continue" for defaults, or say what to add or change.

- **The full table is ALWAYS shown in the first reply** — even when the theme
  is missing (prefill the theme row with "needs input") — together with the
  auto-completion note. Never send a bare clarifying question without the
  table. The table must appear in the visible reply text **before** calling
  `ask_clarification`; the tool's `question` only carries the short confirm
  prompt (e.g. "continue or edit?"), never the table itself.
- `clarification_type`: `missing_info` if there is no theme at all (not even
  a vague one) — then ask for ONLY the theme in the question and do NOT offer
  a "continue" option (there is nothing to continue yet; the table is still
  shown with the theme row marked as missing); otherwise `approach_choice`.
- `options` (theme present): `["Continue with these inputs", "I want to add
  or change something"]`.
- If the user edits any field, update and re-show the complete table. A mode
  edit re-routes: confirm the new mode's material expectations (reference
  needs images — see the collection plan; frame modes need the frame image;
  T2V needs none) and refresh the Images row accordingly.
- Optional fields never block progress. The user confirms the table as a whole;
  do not interrogate field by field.
- **Selection rows render as option lists**: the enum/preset rows (Mode,
  Storyboard images, Images, and the preset slots in Aspect ratio / Resolution)
  show every option with the current choice marked `[✓]`, the rest listed bare —
  the user switches by naming another option. `{...}` rows are free
  text and keep plain prefills.
- **Storyboard-image intent = 是 (one exception to no-field-interrogation)**:
  when the user sets the Storyboard images row to 是 (or asks for storyboard
  design images in their words), the intent AND its limits must be settled at
  THIS gate, before moving on — never deferred to the plan card:
  1. **Cost note in the options/question text** (the only sanctioned place —
     the plan card itself still never shows quota or cost notes): storyboard
     images consume image-generation quota, one image per shot.
  2. **Estimated shot count**: take the duration's shot-budget band ceiling
     (shot budget table — provider-independent; 5 s default → 4–6 s band → 2
     shots) and TELL the user the duration–shot linkage ("5 s supports at most
     2 shots; storyboard work reads better at 7 s or longer").
  3. **On-the-spot limit check** with the known inputs: estimated shots (=
     storyboard image count) + originals expected to be ATTACHED to the video
     call (default 0 — originals are not passed unless they carry information
     the storyboard images don't cover or the user explicitly requires
     likeness to a specific photo; count them only when the user says they
     join the call). Check against the routed provider's reference cap
     (H3 9 / Seedance 2.5 30 via `--describe-provider`; provider here is the
     predicted one — see Choosing the mode for how the provider resolves) AND
     the H3 material budget (storyboard images + attached originals ≤ 5 —
     H3 only; Seedance caps at its reference cap). Over the limit → the user
     chooses on the spot: fewer shots / drop attached originals / switch to a
     provider that supports the plan / drop the storyboard intent. State the
     chosen resolution in the Storyboard images row and re-show the table.
  4. The prompt-writing step (Step 2) re-checks the finalized shot count
     against this settled budget; over → back to the user with the SAME
     options (this is conversational adjustment — no gate, no card; holding
     out has no "force through" exit: staying over the limit means dropping
     the storyboard intent).
- Creative-field precedence: explicit user input > image-determined values >
  semantic completion > fixed defaults. Preserve explicit executable settings
  when compatible with the mode. A frame image's physical ratio is a hard
  constraint — resolve a conflicting requested ratio in Necessary material
  checks, never pretend both apply.
- Once a theme is present and the user confirms the table, proceed to
  mode routing and material checks. Do not write the prompt before this gate.

## Choosing the mode (read this first)

**Capability data (model matrix, ratio enum, parameter support, per-model
defaults, best-for guidance) comes from the adapter manifest — run
`python /mnt/skills/public/video-generation/scripts/generate.py
--describe-provider {provider}` for the routed provider before writing the
plan card. Never read the adapter source.**

User-facing modes are three — T2V, first+last frame (首尾帧), reference.
首尾帧 is the precise-locking choice: use the frame API entries only when the
video must start/end exactly on a given image; softer intent (including the
whole storyboard path) routes to reference with a keyframe declaration.
Internally the three map to the routing entries below. MiniMax H3 supports
them all, selected by `--image-role` plus the images you pass in
`--reference-images`:

| The user gives you | Mode | `--image-role` |
|---|---|---|
| Only text | **T2V** | (omit) |
| Text + one image to start from | **I2V (first frame)** | `first_frame` (default) |
| Text + one image to END on | **last frame** | `last_frame` |
| Text + a start image and an end image | **first + last frame** | `first_last` |
| Text + image(s) of a character/style to imitate | **reference (Ref2VA)** | `reference` |

**Seedance multimodal reference:** Seedance reference mode also accepts
reference videos and reference audios (public URLs). When the user supplies a
reference video or audio (not just images), route to Seedance — MiniMax H3
cannot consume reference video/audio. Pass them via `--reference-videos` /
`--reference-audios`; the prompt labels them `@video1` / `@audio1` in
CLI-pass order (see `references/providers/seedance/prompt-format.md`).

**Frame vs. reference — the key distinction (they are mutually exclusive):**

- A **frame** image (first/last) is an *actual frame of the video* — it literally
  appears on screen at 0:00 (first) or at the end (last). The model fills in the
  motion between frames. Think: "start the video from this photo."
- A **reference** image does not pin an exact frame. It tells the model "the
  character/style looks like this," and the model transfers that identity/look
  into a freshly generated video. A prompt may use it as a soft opening/closing
  composition anchor, but that is not pixel-exact frame preservation. Think:
  "make a video of this person, who looks like this photo."

Because one pins a concrete frame on the timeline and the other transfers
features, H3 does not allow mixing frame roles with reference roles in one
request. Pick the mode from intent: is the image a moment *in* the video (frame)
or a likeness to *imitate* (reference)?

**When the user has not specified a mode, default to reference mode.**
Reference mode needs images. If the user supplied none AND the Images row is
not 无图且不找, do NOT call any provider yet — draft a **collection plan**
(one line per image: purpose — face / outfit / scene / style, count, target
ratio) for the plan card's Materials row: **three images by default**,
adjusted up or down to fit the storyboard. Confirming that plan approves the
**search** stage only. Then collect — **search first** (image search by
purpose; only use results whose subject is clear and usable). When nothing
suitable is found, do NOT silently
fall back to image generation: it spends the image quota, so
**ask the user first** (one question: allow generating the missing reference
images — uses image quota / skip). Only generate after an explicit yes;
otherwise stop and offer: user uploads / switch to pure T2V / revise the
plan. Search may iterate at most TWICE before asking; a refused generation
counts as a stop, not an iteration. Inspect the collected images
(collected-material check), then write the final Ref2VA prompt from the
actual images (rules in Step 2). Pure T2V only
when the user asks for text-only or the scene outranks consistency. Note:
the default adds a collection stage (search always, image generation only
with the user's approval) before video generation; choose T2V explicitly
when visual freedom and a single call matter more.

**Collection timing (storyboard path included):** for reference mode without
user-supplied images, collection happens BEFORE prompt writing (Step 2), not
after the plan card. The Images row's three-state intent drives routing:
有图 → material checks on those images → prompt; 无图帮我找 → collection
plan → collect (rules above) → collected-material checks → prompt;
无图且不找 → skip collection — with the Storyboard images row = 是, the
storyboard images become the prompt's reference assets (first image
generated text-to-image — Step 2.5); with 否 (default), a reference request
without images has nothing to route on — ask the user: explicit T2V or
upload.

**Storyboard-image applicability (decide at routing time, not on the plan
card):** when the Storyboard images row = 是, resolve the provider NOW (same deterministic
rules the script uses — `--model` declared in config, else first configured
provider, else credential fallback; see `references/runtime.md`) and run
`--describe-provider {provider}` (read-only, never billed). Every
reference-capable provider supports storyboard images — the only checks are
reference mode in the manifest (T2V / frame modes / H3-Max fail this) and
the caps settled at the input table (reference cap + H3 budget). Seedance
2.0 family lacks official storyboard guidance, so alignment there is weaker
— note that on the plan card when routed there. Not applicable → tell the
user IMMEDIATELY (before any prompt writing) and offer: switch providers /
drop the storyboard intent. If the routed provider differs from the
input-table prediction, re-run the limit check under its numbers and tell
the user. Silent ignoring is forbidden.

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
  `reference`** and note the choice in the plan card ("using it as a likeness
  reference — say the word if you actually want the video to *start from*
  this image instead").
- **Self-contradictory** (asks both "start from this image" and "match this
  person's face") → only here use `ask_clarification` before writing anything.

Rationale: explicit frame language is the user actively choosing the frame
mode. Defaulting with a correction exit in the plan card beats blocking on
every upload.

### Image count and order per mode

Images map by **position** in `--reference-images` (there is no per-image
label), so order matters — confirm it with the user when it isn't obvious:

| Mode | Images | Order / rule |
|---|---|---|
| `first_frame` | 1 | the single image is the opening frame |
| `last_frame` | 1 | the single image is the closing frame |
| `first_last` | exactly 2 | **first image = opening frame, second = closing frame** — never swap; if only one image is available, route to `first_frame` instead of invoking `first_last` |
| `reference` | multiple, per-provider cap (run `--describe-provider`) | all treated as likeness references; Seedance reference mode also accepts reference videos/audios (URLs) |

Do not pass more images than a mode uses — a 3rd image to `first_last`, or a 2nd
to `first_frame`/`last_frame`, is dropped with a printed warning (the adapter
also hard-rejects reference images over the model's cap — e.g. 9 on H3 —
instead of letting the API reject a billed task), so send only what the mode
takes. For `reference`, name the images in your prompt in the same
order you
pass them (the Ref2VA format labels them `<Picture 1>`, `<Picture 2>`, … by
position — see `references/providers/minimax/prompt-format-ref.md`).

### Necessary material checks

Before prompt writing, check only what is required to continue:

- image count matches the selected mode;
- image role is clear and first/last order is correct;
- frame and reference roles are not mixed;
- images are usable and the subject is basically recognizable;
- `first_last`: the two images' ratios equal or close enough for a coherent
  transition;
- Seedance reference videos/audios are public URLs (the API rejects base64
  and cannot fetch local paths); counts stay within the model's caps (per-model
  numbers via `--describe-provider`);
- Seedance 2.5 frame tasks lock `ratio=adaptive` (the output follows the
  frame image's aspect ratio); only video-edit locks `duration=-1` — an
  explicit non-adaptive `--aspect-ratio` on a 2.5 frame task is rejected
  locally with a clear error;
- images pass the provider's input specs — run the spec preflight once
  materials are in hand (the script reads each provider's spec from the
  adapter manifest; current values via `--describe-provider`):

  ```bash
  python /mnt/skills/public/video-generation/scripts/check_materials.py \
    --images {full paths or URLs, space-separated} \
    --out-dir /mnt/user-data/workspace
  # Seedance materials: add --provider seedance
  ```

  Out-of-spec images are auto-fixed locally (no question needed); use the
  script's output paths as the materials. An image it cannot decode is NOT
  fixable — replace it before writing any prompt. The script never calls the
  provider.
- Seedance + real-person material: if any image may contain a real person's
  face (the user's statement or your visual judgment), run the per-material
  question — drop / replace / redraw each face image per
  `references/workarounds.md` — before any image generation. Never redraw
  silently; MiniMax H3 needs no workaround.

Do not require matching ratios across `reference` images, and do not compare
across images for a single first or last frame. If frame-image ratios conflict
with each other or with an explicitly requested output ratio, use exactly
three exits: ① crop the frame image(s) to the requested or a common ratio
② replace the conflicting image(s) ③ keep the original frame-image ratios —
a single frame keeps the image's native ratio; `first_last` keeps both and
explicitly accepts the transition mismatch. An accepted exception counts as a
passed material check and is never asked again.

## Output settings

Surface these settings in the input table. Preserve explicit choices; otherwise
apply the defaults and show the resolved values on the plan card without a
separate question. Per-provider value domains, defaults, and model guidance
come from `--describe-provider` (see Choosing the mode) — this section keeps
only the provider-independent rules.

| Setting | Rule | Default |
|---|---|---|
| Aspect ratio (`--aspect-ratio`) | T2V: optional; reference mode: optional, defaults to adaptive — **storyboard path: pass the concrete ratio the storyboard images were generated at (default `16:9`; never adaptive)**; frame modes: fixed by the image (do not pass); Seedance 2.5 frame modes force `adaptive` | `16:9` (T2V) / adaptive (reference) |
| Duration (`--duration`) | follow the shot budget in the selected prompt reference; 2+ shots: prefer 6 s or longer | `5` (Seedance 2.5 frame tasks: model-picked, `-1`) |
| Resolution (`--resolution`) | per-model value domain and draft-tier default — run `--describe-provider` (rejected locally if unsupported) | provider draft tier from `--describe-provider` |

Guidance:

- Casual request → draft plan = draft tier (per `--describe-provider`) · 5 s;
  state it on the plan card, don't ask.
- Vertical / social → suggest `9:16`; cinematic → `16:9` or `21:9`.
- H3: prefer drafting at the draft tier and upgrading the take the user likes
  (Step 6); Seedance has no upgrade path, so pick its draft tier. If the user
  explicitly requests direct 2K (H3), preserve that setting in both the input
  information table and the generation plan.
- Explicitly given parameters are prefilled in the input table and preserved in
  the plan; never replace them with defaults.

## Workflow

### Step 1: Confirm inputs, route the mode, and check materials

Apply the input-table, mode-routing, precedence, and material-check rules
above. Material checks run in two layers: **available-material checks** (the
images the user supplied) before prompt writing; **collected-material
checks** (auto-collected images: usable, subject clear) after collection
(happens before prompt writing — Choosing the mode, Collection timing),
before the final Ref2VA prompt is written. Both layers include the
spec preflight (`scripts/check_materials.py`). Never report material checks
as "passed" while planned images do not exist yet.

### Step 2: Write the structured prompt file

Route by provider and mode, then load exactly ONE format reference — the spec
owns the grammar. Routing table (provider → mode → format file):

| Provider | Mode(s) | Format file |
|---|---|---|
| `minimax_h3` | T2V / first frame / last frame / first+last | `references/providers/minimax/prompt-format-base.md` — structured fields/sections, `[Shot N]` timeline, camera motion, speakers and `<d>` dialogue, language exceptions, keyframe soft-anchoring |
| `minimax_h3` | reference (Ref2VA) | `references/providers/minimax/prompt-format-ref.md` — six-section format, `<Picture N>` labels |
| `seedance` | any mode | `references/providers/seedance/prompt-format.md` — `@image1`/`@video1`/`@audio1` material tags in CLI-pass order, time-axis segments, dialogue in quotes; reference mode may mix images + reference videos + reference audios as public URLs |
| `minimax_v1` | any mode | prose (no structured format file — subject + main action first, ONE main camera move, lighting, atmosphere) |

Read the routed file at
`/mnt/skills/public/video-generation/<format file from the table>`, then write
the prompt file in that format. Redraw workaround active (materials are
character design sheets — `references/workarounds.md`): also read that file
for the fixed live-action instruction paragraph and its placement after the
@-tag declarations.

Prompt content language: Chinese by default — switch to English only when the
user explicitly requests an English prompt. Protocol tokens stay in English
exactly as the loaded spec defines them (field names, `[Shot N]`, `<d>` tags,
labels, instruction lines, the English camera-motion clause). Exceptions keep
their original language verbatim: dialogue inside `<d>` and visible on-screen
text.

Write the result to `/mnt/user-data/workspace/{descriptive-name}.txt` — plain
text whose content is the structured format (instruction line for keyframe
modes, then the fields). The structured format is still plain text, never a raw
JSON blob.

Other providers (`minimax_v1`) keep the prose methodology: subject + main
action first, ONE main camera move, lighting, atmosphere, ~60–100 words for a
single-beat clip (audio description is H3-only); same content-language rule —
Chinese by default, English on explicit request.

Ref2VA prompts must be written from the ACTUAL user images — their real
subjects, features, and `<Picture N>` order. When reference images are
auto-collected, write this prompt only after collection and the
collected-material check; never fill the word budget by describing
planned-but-unseen images. **Storyboard-path exception:** the storyboard
design images are the one planned-but-unseen asset the prompt MAY define in
advance — the prompt is written BEFORE the storyboard images exist, declaring
each image's shot role (format rules in the provider's storyboard section:
`references/providers/{minimax|seedance}/prompt-format*.md`). User-supplied
references must still come from actual files. The storyboard images are
generated right after prompt writing (Step 2.5) and confirmed verbatim on
the plan card.

### Step 2.5: Generate storyboard design images (storyboard path only)

Run this step ONLY when the Storyboard images row = 是 passed all checks
(input-table intent + routing-time applicability + limit check + shot-count
re-check). Generated BEFORE the plan card so the card confirms real files.

**Generation rules:**

- **First shot** — by the Images row's collection intent:
  - 有图 or 无图帮我找 (collected) → `image-editing` with the user's /
    collected material as the base image (identity/consistency anchor;
    redraw workaround active → the design sheet replaces the photo as the
    anchor — `references/workarounds.md`):
    ```bash
    python /mnt/skills/public/image-editing/scripts/edit.py \
      --image {base material path} \
      --prompt {first-shot description: framing, subject pose/action, scene; keep identity, style, palette} \
      --output-file /mnt/user-data/outputs/{name}-sb-1.png \
      --quality medium
    ```
  - 无图且不找 → `image-generation` text-to-image fallback (the input-table
    approval covers this; do NOT re-ask). Quality is pinned via env inline
    (`OPENAI_IMAGE_QUALITY=medium` — the image-size map stays untouched; the
    size drop is NOT done via `OPENAI_IMAGE_MODE=chat_completions`, which
    changes the response protocol):
    ```bash
    OPENAI_IMAGE_QUALITY=medium python /mnt/skills/public/image-generation/scripts/generate.py \
      --prompt-file /mnt/user-data/workspace/{name}-sb-1.json \
      --aspect-ratio {target ratio, default 16:9} \
      --output-file /mnt/user-data/outputs/{name}-sb-1.png
    ```
  By default the storyboard images are the ONLY reference assets passed to
  the video call; original materials join only when they carry visual
  information the storyboard images don't cover or the user explicitly
  requires likeness to a specific photo (the card then lists them as
  attached). One image per shot; EVERY subsequent shot chains from the
  FIRST shot's image via `image-editing` (change only
  景别/动作/机位/场景; identity, clothing, palette, style stay anchored to
  shot 1) — never from the previous shot (previous-shot chaining forces
  serial generation and compounds drift). **Dispatch shots 2..N in
  parallel**: once shot 1 succeeds, issue all remaining shots in the SAME
  response as independent bash calls (or one call with `&` + `wait`) —
  never one-by-one waiting for each. Never `&&`-chain shots (a
  short-circuit would still bill reserved calls); `;` is sequential, not
  parallel.
- **Prompt-file JSON** (image-generation only): `{ "prompt": "..." }` — the
  shot description verbatim (subject, framing, action, scene, style; no
  storyboard jargon inside the image prompt).
- **Ratio**: generate at the plan's output ratio (default 16:9, explicit user
  ratio wins). image-editing has no ratio parameter — the output follows the
  base image; if the base image's ratio conflicts with the target, the
  storyboard image lands on the nearest in-spec ratio with the same
  orientation (`check_materials.py` preflight still applies; it validates, it
  never crops to a target). State the landed ratio on the plan card if it
  differs from the target.
- **Failure**: stop at the first failed image — report the error, do not
  retry on your own (each attempt consumes image quota even on failure).
  Wait for the user; a retried image costs one more image call.
- **Resume**: on a new run with the storyboard half-generated, reuse existing
  on-disk storyboard files (`{name}-sb-N.png`), generate only the missing
  shots.
- After all images exist, run the spec preflight (`check_materials.py` with
  the routed provider) on them, then proceed to Step 3.

**Plan-card integration:** the storyboard images appear in the plan card's
Materials row (source-labeled; when originals are not passed the card notes
"original materials used only to generate the storyboard images"), and the
Storyboard row lists them shot-by-shot (shot no. ↔ timestamp ↔ path) plus the
usage declaration: passed in shot order as reference images, for composition
and content only — their art style and any in-image text are NOT adopted.
The plan card is a PRODUCT check (look at the images, then approve) — intent
and limits were already settled at the input table.

### Step 3: Build, self-check, and confirm the fixed plan card

Creating the plan card and performing the final self-check are one workflow
state, not two. Before showing the card, verify that the confirmed theme has
been decomposed into a concrete subject + action in the prompt, the
material checks in Step 1 passed, output settings have values, the prompt
file exists and its full content is shown on the card, and the
user-facing refinement/storyboard exists. In reference mode without an explicit
ratio, the plan card shows `adaptive` — never invent a concrete ratio. Return to
the relevant earlier step
if an essential is missing.

Create a concise **user-language refinement** from the internal prompt — not a
copy of it. Preserve the subject, action, setting, style/mood, camera
movement, and material role; add a short storyboard only when needed (`None`
for a simple single-shot clip).

Call `ask_clarification` with this fixed template, translated to the user's
language:

```text
[Video generation plan]

- Mode: {T2V / first + last frame / reference}
- Materials: {none / FULL paths with roles and order / storyboard path: storyboard images, one per shot (source-labeled; original materials used only to generate them are NOT passed) + originals attached only when they carry information the storyboard images don't cover / collection plan: purpose, count, ratio per image — search first; image generation only after user approval}
- Output: {duration} · {resolution} · {explicit ratio / adaptive — storyboard path: the concrete ratio the storyboard images were generated at}
- Prompt: {workspace path}
  Refined description:
  {subject, action, setting, style/mood, camera, and sound as needed in the user's language}
- Storyboard:
  {None / concise storyboard in the user's language / storyboard path: shot-by-shot list — shot no. ↔ timestamp ↔ image path — plus the usage declaration (passed in shot order as reference images; composition and content only; art style and in-image text NOT adopted)}
- Changes this round:
  {Initial plan / changes from the latest superseded plan}
- Capability note:
  {None / This 4 s draft cannot be upgraded to 2K; use 5 s or longer to keep the upgrade option}

Reply "confirm" to start, or tell me what to change.
{Storyboard path only: not happy with a storyboard image? Say which shot and what to change — the affected images are regenerated in this conversation; video generation starts only after you confirm the storyboard images.}
```

The plan card is fully transparent about what will be submitted: materials
are listed by their full paths, and the prompt file is given by its path
(prompt + user-language refined description only) — the user opens the
presented file to review the actual input. The originals are
also opened via `present_files` before the card is shown: copy the prompt
file to `/mnt/user-data/outputs/{name}.prompt.txt` and every material to
`/mnt/user-data/outputs/{name}-materials/` (use the final images that will
be submitted — fixed files replace their sources — and include
user-uploaded images), then present them all. Re-copy on any change
(contents update in place; the artifact list stays the same).
The card must not show the model, quota, or cost notes. The 4 s upgrade
limitation is a capability note, not a cost note; show it only when the user
explicitly selects a 4 s 768P draft.
Direct 2K output does not need an upgrade warning.
Redraw-workaround path: the Materials row lists the character design sheets
(source-labeled, redrawn from real-person photos) and declares the result is
an AI re-enactment from them; the image-quota cost was declared at the
per-material question and is not repeated on the card.

- `clarification_type`: `approach_choice`.
- `options`: `["Confirm and generate", "I want to adjust"]`.

Confirmation protocol (prevents loops and skips):

- **Affirmative** reply (confirm / okay / go ahead / 可以 / 生成吧) → execute
  Step 4 immediately; do not re-ask anything optional.
- **Adjustment** reply → revise the prompt file, user-facing refinement,
  storyboard, settings, or materials as needed, then re-show the FULL fixed
  card with a change summary. No round limit; never advance without fresh
  approval. Re-run material checks if images change. **Storyboard-image
  adjustments use affected-only regeneration**: regenerating one shot keeps
  the other shots (each attempt costs one image call); changing the shot
  count redoes only affected shots (fewer shots → discard the surplus
  images; more shots → generate only the new ones); regenerating shot 1 or
  changing the original materials invalidates every downstream shot derived
  from them — regenerate the chain. Dropping the storyboard intent on the
  card discards the storyboard images, reverts the prompt to the plain
  reference formulation, re-routes, and re-shows the card.
- **Resume across runs**: the gate ends the current run; the user's next
  message starts a new one. The LATEST plan card in history is the only live
  one — execute only that approved plan, never an older card. If a run ends
  mid-storyboard-generation (before any card), the next run resumes that
  generation (reuse on-disk storyboard files, generate only missing shots).
- **Ambiguous or non-committal** reply (for example, "嗯" or "ok?") → remain at
  the gate and ask for an explicit confirmation. Never execute without one.

### Step 4: Execute

Use the confirmed Output settings. Pass `--resolution 2K` only when direct 2K
appeared in the latest approved plan card.

```bash
# T2V (text only). Mode-specific commands: references/providers/{minimax|seedance}/examples.md
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{name}.txt \
  --output-file /mnt/user-data/outputs/{name}.mp4
```

Parameters:

- `--prompt-file` (required): structured `.txt` (H3), `@`-tag `.txt` (Seedance),
  prose `.txt`, or a `.json` file with a top-level `"prompt"` string field (only
  that field is used).
- `--reference-images`: image path(s), space-separated; omit for T2V. Meaning
  depends on `--image-role`.
- `--reference-videos` / `--reference-audios` (Seedance reference mode): public
  URLs only — the API rejects base64 and cannot fetch local paths. Labeled
  `@video1`/`@audio1` in the prompt in CLI-pass order.
- `--image-role`: `first_frame` (default) / `last_frame` / `first_last` /
  `reference` (H3 and Seedance; unsupported on `minimax_v1`).
- `--output-file` (required): output `.mp4` under `/mnt/user-data/outputs/`,
  must NOT already exist.
- `--aspect-ratio`: per-mode semantics — see Output settings.
- `--model` / `--provider`: routing + escape hatch — `references/runtime.md`.
- `--resolution`: per-model value domain and draft-tier default —
  `--describe-provider`; validated locally.
- `--duration`: per-model range (default 5 keeps the H3 2K upgrade open;
  Seedance 2.5 also accepts `-1` auto) — `--describe-provider`.
- `--query` / `--cancel`: read-only task lookup / cancel a queued task —
  `references/task-lifecycle.md`.

[!NOTE]
Do NOT read the python file, instead just call it with the parameters.

Reference mode with an approved collection plan: collection happens BEFORE
prompt writing (Choosing the mode, Collection timing). By the plan card the
collected images have passed the spec preflight and been named in the prompt.
The card already showed them; generate the video directly on
confirmation. **Storyboard path**: the storyboard images were generated in
Step 2.5 and confirmed on the card — pass them in the order the prompt
declares (originals only when the card lists them as attached),
`--aspect-ratio` = the card's concrete ratio, and generate immediately on
confirmation.

Before dispatching, check the sidecar for an unfinished duplicate. A local
polling `timeout` is not an upstream terminal status — resolve it read-only
with `--query`: `succeeded` → download to a NEW output path, then Step 5
delivery; `failed` → offer a fresh plan; still active (`queued` /
`processing` / `pending` / `running`) → report and wait.
Never auto-resubmit without the user's explicit go-ahead. If `generate.py` or any call errors, stop immediately — never self-retry or debug (paid API, each call consumes quota); report the error + task id and wait for the user. Full state machine:
`references/task-lifecycle.md`. Execution is one foreground call — no live
progress; per-poll elapsed times and a stage summary arrive at the end.

### Step 5: Present the result + iteration exits

Before delivery, verify the generation succeeded, the output path is known,
and the video file exists and is usable; on failure or polling timeout, report
the error or task id instead. Then `present_files` the video (video first,
then any generated reference image), describe it briefly, and append the
iteration exits in the user's language. Every success offers three exits:

```text
Happy with it? I can: ① tweak the prompt ② change duration/resolution
③ swap the input image(s).
```

Append `④ upgrade this take to 2K (same content, refined details)` ONLY when
all conditions hold: the result is a MiniMax **H3** (not H3-Max) 768P draft,
its duration is at
least 5 s, and its source video is available to the regeneration endpoint.
Never show the upgrade exit for a 4 s draft; offer only ①–③ so the user
cannot enter an impossible upgrade flow. Prompt/setting changes return to
prompt writing; image changes return to mode routing, then material checks
and prompt rewriting (a different image may change the mode). Routes ①–③
each re-pass the full fixed plan card (④ uses the Step 6 gate); new output
filenames per Iteration. Do not repeat the
input-table gate unless the user starts a new request or invalidates the
confirmed theme.

### Step 6: Upgrade to 2K (optional, MiniMax H3 only)

The upgrade runs a short confirmation gate showing the source video path and
"content corresponds, details re-rendered, 768P→2K". Do not include model,
quota, cost, or the full prompt in the user-facing card. Execute only after an
explicit confirmation; cancellation returns to Step 5. A prompt, material, or
setting change starts a new generation plan rather than changing the upgrade.
Hard constraints for the command:

- Reuse the ORIGINAL prompt file and reference images — same paths, order,
  `--image-role`, and `--model` (the endpoint replays the exact original
  input; a changed input is a new generation, not an upgrade).
- `--output-file` must be a NEW path, e.g. `{name}-2k.mp4` (never overwritten).
- A local source above ~45 MB is rejected (request-body cap). Point
  `--upscale-video` at a public URL, or draft a shorter one — a new
  generation that must pass the full plan-card gate.

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

## Examples

Example A shows the full T2V flow with both gates, Example E the 2K upgrade.
Worked commands for the other modes live in the routed provider's examples —
`references/providers/minimax/examples.md` (MiniMax) or
`references/providers/seedance/examples.md` (Seedance) — load it after the
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
  --output-file /mnt/user-data/outputs/cat-stretch.mp4
```

Present with exits ①–③ plus ④ (eligible 5 s draft on the H3 upgrade path).

### Example E — upgrade the take to 2K

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

## Provider constraints

Per-provider value domains, draft-tier defaults, reference caps, image roles,
and parameter support come from the adapter manifest — run
`--describe-provider` (see Choosing the mode). Credentials and behavior notes
per provider: `references/providers/minimax/spec.md` /
`references/providers/seedance/spec.md`.

`--query` works on every provider; `--cancel` is `minimax_h3`/`seedance` only
(errors on legacy `minimax_v1`). Frame and reference roles are mutually
exclusive; reference videos/audios are Seedance-only (MiniMax H3 cannot
consume them); avoid named real people or trademarked characters.
Routing/credentials/compatibility: `references/runtime.md`.

## Iteration (regenerate, not edit)

This skill does NOT edit an existing video — iterating means changing the
prompt, materials, or settings and generating a brand-new take. Generation is
non-deterministic (no fixed seed): any change re-rolls the whole clip; the
ONLY same-content path is the 2K upgrade (Step 6). A fixed first frame is the
most controllable iteration. Always use a NEW output filename and regenerate
deliberately, never blindly.

## Delegation boundary

Single-video requests run on the lead agent because the input-table and
plan-confirmation gates must interrupt the user-facing conversation. For
batch or multi-segment work (>15 s stitching), confirm the aggregate plan
first, then delegate per-segment execution; model preference carries over.
