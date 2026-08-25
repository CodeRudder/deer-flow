> Structured prompt format for MiniMax H3 base modes (T2V / first frame / last
> frame / first+last). Derived from the official MiniMax H3 Prompting Guidance
> (base modes) and adapted to this skill's CLI (`--duration` 4–15 s default 5,
> `--aspect-ratio` T2V only, `--image-role` mode selection). H3-specific — other
> providers keep the prose methodology.

# Base-Mode Structured Prompt Format

Load this before writing a prompt file for T2V / first-frame / last-frame /
first+last modes. Output ONE structured plain-text prompt: the instruction line
(keyframe modes only), one blank line, then the three core fields.

## Input you are working from

- **BRIEF**: the user's request (any language) plus any mode decision already
  made in SKILL.md Step 1.
- **MODE**: `t2va` | `i2va` | `fl2va` | `l2va` — mapped from `--image-role`
  and the images passed (no image → t2va; `first_frame` → i2va; `last_frame`
  → l2va; `first_last` → fl2va).
- **KEYFRAMES** (i2va/fl2va/l2va only): the attached frame image(s).
- **TARGET**: `--duration` seconds (4–15, default 5); `--aspect-ratio` only
  exists for t2va — image modes inherit the ratio from the image.

## Output contract (absolute)

1. The prompt file contains ONLY the instruction line (keyframe modes), one
   blank line, then the three fields with exact lowercase names followed by a
   colon. No preamble, no explanation, no markdown fences.
2. Write everything in English. EXCEPTIONS: dialogue/lyrics inside `<d>` and
   visible on-screen text keep their original language verbatim.
3. All timestamps use MM:SS.mmm, are strictly increasing, and fall within the
   duration. Where S.SS appears, format the duration to exactly two decimals
   (e.g. 8 s → 8.00).

## Instruction line (first line, keyframe modes only — copy exactly)

### i2va

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
```

### fl2va

```
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
```

### l2va

```
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
```

N = index of the final shot; S.SS = effective duration to two decimals. t2va
has no instruction line and begins directly with the fields.

## The three core fields

### `integrated_multimodal_description`

The timed multi-shot timeline: visuals, actions, shots, speakers, dialogue,
singing, and diegetic audio in playback order. Rules below.

### `overall_soundscape`

1–4 English sentences, one paragraph: ambience + physical action sounds +
non-verbal human sounds across the FULL video. No dialogue/singing/diegetic
music here (those belong in the shot description). `N/A` only for explicit
total silence.

### `non_diegetic_music`

1–3 English sentences about score the CHARACTERS CANNOT hear: instrumentation,
tempo, rhythm, dynamics only — no mood words. Music audible to characters
(radio, singing, phone) is diegetic and belongs in the shot description. `N/A`
when there is no score.

## Multi-shot planning

### Shot budget

| Duration | Shot count |
|---|---|
| 4–6 s | 1–2 shots |
| 7–10 s | 2–3 shots |
| 11–15 s | 3–5 shots |

Respect an explicit user shot count/plan first. Each shot needs at least
~1.5–2.0 s. Never more than ONE dominant action per shot. A single-shot video
still deserves a full description.

### Timeline syntax

- `[Shot 1]` has NO timestamp and MUST open with the overall style + initial
  composition. Styles: cinematic live-action, 2D animation, 3D CG, claymation,
  watercolor, vintage film… For keyframe modes derive style from the frame
  image; for t2va from the brief.
- Later shots: `[Shot N] At MM:SS.mmm, the camera cuts to …` — strictly
  increasing cut times.
- Cut verbs: "the camera cuts to", "the shot cuts/transitions/changes/switches
  to". Cross-dissolve/fade/wipe ONLY when the user explicitly asked.

### Cut logic — cut vs. camera motion

- A cut must introduce NEW information: new subject, space, state, viewpoint,
  or time. If only the framing distance or a slight angle changes, use camera
  motion inside the current shot, NOT a cut.
- End each shot on a beat the next shot can pick up (action mid-motion, a
  glance, a sound cue).

### Continuity across shots

- Repeat identity anchors EVERY shot (appearance, clothing, key props) —
  phrased freshly but consistently.
- Track state changes: what got wet/opened/taken/broken in shot N stays that
  way in shot N+1.
- Preserve screen direction and relative positions across cuts unless a shot
  deliberately re-establishes geography.
- Ambience flows across cuts; dialogue crossing a cut follows the `<d>` rules
  below.

## Shared grammar

### Camera motion = type + amplitude + speed

- Types: Zoom In/Out, Push In/Pull Out, Pan Left/Right, Truck Left/Right, Tilt
  Up/Down, Pedestal Up/Down, Arc Shot, Tracking Shot, Static Shot, Shake
  Slightly/Shake Strongly, POV, Roll Clockwise/Counterclockwise.
- Amplitude: "with small amplitude" / "with large amplitude" (omit if medium).
- Speed: "at slow speed" / "at fast speed" (omit if normal).

Write it as natural English, not stacked labels:
`The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.`

### Speakers, dialogue, singing

- Vocal sources get stable IDs (S1), (S2)… assigned in order of first vocal
  event and reused at EVERY later vocal event; group speech (S1,S2).
  Characters who never vocalize get NO ID.
- First appearance: identity anchors (type, age, gender, on/off-screen, pitch,
  timbre, rate, accent).
- Format: identifying phrase + ID + delivery OUTSIDE `<d>`; inside `<d>` ONLY
  the language tag and the exact words:
  `The young woman with a quiet, breathy voice (S1) says: <d>[English] I get off at the next station.</d>`
  — preserve the user's words and punctuation verbatim; never translate or
  rewrite.
- Voiceover: use the exact phrase "says in an off-screen voiceover" and
  immediately state the on-screen lips stay closed: "…while his lips remain
  completely closed."
- Dialogue crossing a cut: `<d>` tags at the connecting points in both parts +
  an explicit continuity statement ("continues seamlessly across the cut",
  "carries over from the previous shot"). Speech cut off by the video end:
  `</d>`.

### On-screen text

Visible banners/signs/labels/subtitles/neon in English double quotation marks,
verbatim, no translation:
`A red neon sign reading "营业中" glows above the doorway.`

## Per-mode body strategy

- **t2va**: build the full timeline from the brief; you may add consistent
  scene/character/sound details.
- **i2va**: `[Shot 1]` anchors on the first frame — establish the image's
  style, subjects, composition, and scene anchors (identity, clothing, colors,
  key objects, spatial relationships stay consistent), then develop forward:
  first-frame anchor → action onset → continuous development → result/reaction.
  Direct MOTION; do not re-describe static frame content.
- **fl2va**: DEFAULT TO A SINGLE SHOT so the model interpolates continuously
  first→last; multiple shots only when the user explicitly asked. Structure:
  first-frame state → observable intermediate changes → progressively
  narrowing differences → last-frame state, landed by the final `[Shot N]`.
- **l2va**: `<Picture 1>` is the final frame, belonging to the last `[Shot N]`
  — not to Shot 1. Structure: plausible preceding state → explicit
  action/transition path → gradual convergence in the final shot → exact
  landing on the last-frame image (arrangement, position, camera angle,
  lighting, composition).

## Fidelity rules

- Stay on the user's intent; supplement missing details only when consistent
  with the brief.
- Conform silently to hard constraints (duration 4–15 s, shot budget); never
  explain in the file.
- Avoid named third-party IP, real celebrities, trademarked characters —
  describe generically.
- When the brief omits the dialogue language, default to `[English]`.

## Worked example (t2va, 5 s, two shots)

Request: "Make a short clip of a cat stretching on a windowsill in the
morning." (defaults: 768P · 5 s · 16:9)

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic. A medium wide shot frames a ginger cat stretching on a sunlit wooden windowsill beside a potted plant, arching its back and extending one paw toward the pot. Morning golden-hour light streams through sheer curtains, casting soft volumetric rays across its fur. The camera pushes in with small amplitude at slow speed from the medium shot toward the cat. [Shot 2] At 00:03.000, the camera cuts to a close-up of the paw reaching the pot's rim, dust motes drifting through the light beam, while the stretch's lazy contentment carries over from the previous shot.

overall_soundscape: Gentle birdsong outside, the soft rustle of curtains in a light breeze, a faint creak of the wooden sill under the cat's weight, and a low contented purr.

non_diegetic_music: N/A
```
