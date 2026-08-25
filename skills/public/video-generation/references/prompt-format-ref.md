> Structured prompt format for MiniMax H3 reference mode (Ref2VA,
> `--image-role reference`). Derived from the official MiniMax H3 Full-
> Reference Mode output format guide and adapted to this skill: reference
> images arrive only via `--reference-images` (up to 9, no per-image label
> parameter), so `<Video N>`/`<Audio N>` labels do not exist here. H3-specific
> — other providers keep the prose methodology.

# Ref2VA Structured Prompt Format

Load this before writing a prompt file for reference mode. Output ONE
structured plain-text prompt with exactly six sections, in this order, with
these exact lowercase field names followed by a colon.

## Input you are working from

- **BRIEF**: the user's request (any language) plus the mode decision.
- **ASSETS**: the images passed to `--reference-images`, in order. The k-th
  path in `--reference-images` is `<Picture k>` — the order you pass them on
  the command line defines the labels, and the prompt must name them in that
  same order.
- **TARGET**: `--duration` seconds (4–15, default 5). The aspect ratio follows
  the reference images; do not pass `--aspect-ratio`.

## Output contract (absolute)

1. Output ONLY the six sections below, in order, with exact lowercase field
   names followed by a colon. No preamble, no explanation, no markdown fences.
2. Write every section in English. EXCEPTIONS: dialogue/lyrics inside `<d>`
   and text visibly present in the scene keep their original language
   verbatim.
3. Never invent reference labels beyond those defined in
   `subject_definitions`. A label keeps one fixed meaning across all sections.

## Section 1 — `subject_definitions`

One line per referenced item that must be tracked. Two label types exist in
this skill (image-only input):

- **`<Subject N>`** — reusable VISIBLE content abstracted from reference
  images: people, animals, objects, scenes/environments, clothing, props,
  styles, actions, expressions, poses. State what it is, which picture(s) it
  comes from, and the concrete features to preserve (face, hairstyle,
  garments, accessories, palette). One subject may combine assets:
  `<Subject 1> is the man whose face comes from <Picture 1> and whose outfit comes from <Picture 2>.`
- **`<Picture N>`** — use ONLY when the image itself is a concrete
  frame/composition anchor (opening frame, keyframe, closing frame,
  storyboard). If an image merely defines a character/scene/style, cite it
  inside the `<Subject N>` line instead — no separate picture entry.

### Keyframe soft-anchor (first frame + references combined)

Official reference roles include a "keyframe" role: a reference image may be
declared to act as the video's opening or closing frame. To use it, give that
image its own `<Picture N>` line, anchor it concretely in
`detailed_description` ("the shot begins from `<Picture 1>`"), and use the
`[keyframe completion]` summary prefix. This is a prompt-level soft anchor —
the model strongly follows it but pixel-exact stitching is not guaranteed;
when a seam must be pixel-exact, use the frame modes (`--image-role
first_frame`/`first_last`) instead, which are mutually exclusive with
reference mode.

## Section 2 — `summary`

One short paragraph. MUST begin with a square-bracketed task-type prefix
(never repeat a type; combine with `+` when several apply):

- `[reference generation]` — assets guide generation (character, scene,
  style, action, camera, storyboard) without being a frame anchor.
- `[keyframe completion]` — an image is a concrete frame anchor (see above).

(The upstream format also defines `[video editing]`, `[video continuation]`,
`[audio reuse]`, `[audio reference]` — not reachable with image-only input;
do not use them.) Use only labels defined in Section 1.

## Section 3 — `retention_analysis`

One line per label from `subject_definitions`, using ONLY these fixed visual
markers:

| Marker | Meaning |
|---|---|
| `fully_preserved` | the defined role of the referenced content is fully preserved |
| `partially_preserved` | still used, but some defined characteristics are changed or only partially retained |
| `attribute_transfer` | referenced characteristics are transferred to a different identifiable target subject |
| `weak_reference` | only broad similarity in style, category, composition, or atmosphere is retained |

Format:

```
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - <which features are retained>.
<Picture 1> (appears in [Shot 1]): fully_preserved - <which features are retained>.
```

Newly added actions, backgrounds, or plot are NOT losses of reference
fidelity. Never write `(Sx)` speaker IDs in this section.

## Section 4 — `detailed_description` (main body)

- **Length**: 350–500 English words for generation tasks. Dialogue-dense
  content prioritizes the complete spoken timeline over word count.
- **Opening**: the overall style in 1–2 English sentences BEFORE `[Shot 1]`
  ("The target video uses a cinematic live-action style with soft lighting."),
  never inside `[Shot 1]`.
- **Shots**: `[Shot 1]` has NO timestamp; later shots `[Shot N] At MM:SS.mmm,
  …` with strictly increasing cut times inside the duration. Cut verbs: "the
  camera cuts to", "the shot cuts/transitions/changes/switches to". A cut must
  add NEW information; if only distance or angle changes, use camera motion.
  Cross-dissolve/fade/wipe only if the user explicitly asked.
- **Camera motion**: type + amplitude + speed as natural English (same
  grammar as the base format: Zoom/Push/Pull/Pan/Truck/Tilt/Pedestal/Arc/
  Tracking/Static/Shake/POV/Roll; "with small/large amplitude"; "at slow/fast
  speed").
- **Reference labels**: insert at each label's first appearance and wherever
  its role applies; reuse without redefining. Natural anchor phrasing: "the
  shot begins from `<Picture 1>`".
- **Speakers**: stable IDs (S1), (S2)… assigned in order of actual vocal
  events, reused at every later vocal event; group speech (S1,S2); characters
  who never vocalize get NO ID. At first appearance give identity anchors
  (type, age, gender, on/off-screen, pitch, timbre, rate, accent). When a
  referenced subject speaks, keep both labels:
  `<Subject 1> (S1) turns and says, <d>[English] Wait for us!</d>`
- **Dialogue format**: identifying phrase + ID + delivery OUTSIDE `<d>`;
  inside `<d>` ONLY the language tag and the exact spoken words. Preserve the
  user's words and punctuation verbatim — never translate or rewrite. Default
  language when unspecified: `[English]`.
- **Voiceover**: the exact phrase "says in an off-screen voiceover", then
  immediately after `</d>`: "…while his lips remain completely closed."
- **Dialogue crossing a cut**: `<d>` tags at the connecting points in both
  parts plus an explicit continuity statement. Speech truncated by the video
  end: `</d>`.
- **On-screen text**: visible banners/signs/labels/subtitles/neon in English
  double quotation marks, verbatim, no translation.
- **Per-shot content**: describe only what is visible or audible; establish
  composition, subject appearance and position, environment and lighting,
  actions and state changes, camera movement, current sound, and where
  referenced content appears. Never reduce to a plot summary or a list of
  reference relationships. ONE dominant action per shot.

## Section 5 — `overall_soundscape`

1–4 English sentences, one paragraph: ambience, physical action sounds,
non-verbal human sounds across the FULL video. Do not repeat dialogue,
singing, or shot-synced sound events here. `N/A` only if the user explicitly
requests complete silence.

## Section 6 — `non_diegetic_music`

1–3 English sentences about score the CHARACTERS CANNOT hear: instrumentation,
tempo, rhythm, dynamic changes only — no abstract mood words. Music audible
to characters is diegetic and belongs in `detailed_description`. `N/A` when
there is no score.

## Fidelity rules

- Stay on the user's intent; supplement missing or underspecified details only
  when consistent with the brief.
- Conform silently to hard constraints (duration 4–15 s, ≤ 9 images); never
  explain in the file.
- Avoid named third-party IP, real celebrities, trademarked characters —
  describe generically.

## Worked example

Request: face photo + separate outfit photo, "make a video of this person
wearing this outfit, waving hello." Passed as
`--reference-images face.jpg outfit.jpg` (face first — the prompt names it
first). Defaults: 5 s.

```
subject_definitions:
<Subject 1> is the young man whose face comes from <Picture 1>, with short dark hair, warm brown eyes, and a friendly round face.
<Subject 2> is the outfit from <Picture 2>: a light denim jacket over a white crew-neck t-shirt.

summary:
[reference generation] The target video shows <Subject 1>, dressed in <Subject 2>, standing in a bright modern studio and waving hello at the camera with a warm smile.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - the man's facial identity, short dark hair, and friendly expression are retained.
<Subject 2> (appears in [Shot 1]): fully_preserved - the light denim jacket and white t-shirt are retained exactly.

detailed_description:
The target video uses a realistic photographic style with soft, even studio lighting and a clean light-grey backdrop.
[Shot 1] A medium shot frames <Subject 1>, the young man with short dark hair and a friendly round face from <Picture 1>, wearing the light denim jacket and white crew-neck t-shirt from <Picture 2>. He stands relaxed, weight on his back foot, facing the camera. He raises his right hand to shoulder height and waves hello with a slow, open-palmed side-to-side motion, his smile broadening as he does. The camera holds a static shot for the first half of the clip, then pushes in with small amplitude at slow speed until the wave and the smile fill the frame.

overall_soundscape:
A quiet studio room tone with the soft rustle of the denim jacket as the arm raises, and a faint breath of a chuckle accompanying the smile.

non_diegetic_music:
N/A
```
