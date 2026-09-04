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
- **TARGET**: `--duration` seconds (4–15, default 5). The aspect ratio defaults
  to adaptive; pass `--aspect-ratio` only when the user explicitly requested a
  ratio.

## Output contract (absolute)

1. Output ONLY the six sections below, in order, with exact lowercase field
   names followed by a colon. No preamble, no explanation, no markdown fences.
2. Write all free-form content (definitions, summary, retention features,
   description, soundscape, music) in Chinese by default; use English for the
   whole content ONLY when the user explicitly requests an English prompt.
   EXCEPTIONS (always original language, verbatim): dialogue/lyrics inside
   `<d>` and text visibly present in the scene.
3. Protocol tokens stay in English exactly as specified below and are never
   translated: field names, `<Picture N>` / `<Subject N>` labels, summary
   prefixes (`[reference generation]`, `[keyframe completion]`), retention
   markers (`fully_preserved` …), the `(appears in [Shot N])` skeleton,
   `[Shot N]` with the `At MM:SS.mmm` opener, cut verbs, the natural English
   camera-motion clause, speaker IDs `(S1)`, `<d>` tags, language tags
   (`[Chinese]`, …), the exact voiceover phrases, and `N/A`.
4. Never invent reference labels beyond those defined in
   `subject_definitions`. A label keeps one fixed meaning across all sections.

## Section 1 — `subject_definitions`

One line per referenced item that must be tracked. Two label types exist in
this skill (image-only input):

- **`<Subject N>`** — reusable VISIBLE content abstracted from reference
  images: people, animals, objects, scenes/environments, clothing, props,
  styles, actions, expressions, poses. State what it is, which picture(s) it
  comes from, and the concrete features to preserve (face, hairstyle,
  garments, accessories, palette). One subject may combine assets:
  `<Subject 1> 是一名男子，脸部来自 <Picture 1>，衣着来自 <Picture 2>。`
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
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - <保留的特征，中文>.
<Picture 1> (appears in [Shot 1]): fully_preserved - <保留的特征，中文>.
```

Newly added actions, backgrounds, or plot are NOT losses of reference
fidelity. Never write `(Sx)` speaker IDs in this section.

## Section 4 — `detailed_description` (main body)

- **Length**: 500–800 Chinese characters for generation tasks. Dialogue-dense
  content prioritizes the complete spoken timeline over word count.
- **Opening**: the overall style in 1–2 sentences (content language) BEFORE
  `[Shot 1]` ("目标视频采用电影实拍风格，光线柔和。"), never inside
  `[Shot 1]`.
- **Shots**: `[Shot 1]` has NO timestamp; later shots `[Shot N] At MM:SS.mmm,
  …` with strictly increasing cut times inside the duration. Cut verbs: "the
  camera cuts to", "the shot cuts/transitions/changes/switches to". A cut must
  add NEW information; if only distance or angle changes, use camera motion.
  Cross-dissolve/fade/wipe only if the user explicitly asked.
- **Camera motion**: type + amplitude + speed as a natural English clause
  (protocol — same grammar as the base format: Zoom/Push/Pull/Pan/Truck/Tilt/
  Pedestal/Arc/Tracking/Static/Shake/POV/Roll; "with small/large amplitude";
  "at slow/fast speed"), embedded as-is in the Chinese description.
- **Reference labels**: insert at each label's first appearance and wherever
  its role applies; reuse without redefining. Natural anchor phrasing:
  "镜头从 <Picture 1> 画面开始".
- **Speakers**: stable IDs (S1), (S2)… assigned in order of actual vocal
  events, reused at every later vocal event; group speech (S1,S2); characters
  who never vocalize get NO ID. At first appearance give identity anchors
  (type, age, gender, on/off-screen, pitch, timbre, rate, accent). When a
  referenced subject speaks, keep both labels:
  `<Subject 1> (S1) 转过身来说道: <d>[Chinese] 等等我们！</d>`
- **Dialogue format**: identifying phrase + ID + delivery OUTSIDE `<d>`;
  inside `<d>` ONLY the language tag and the exact spoken words. Preserve the
  user's words and punctuation verbatim — never translate or rewrite. Default
  language when unspecified: `[Chinese]`.
- **Voiceover**: keep the exact English phrases (protocol) — "says in an
  off-screen voiceover", then immediately after `</d>`: "…while his lips
  remain completely closed."
- **Dialogue crossing a cut**: `<d>` tags at the connecting points in both
  parts plus an explicit continuity statement. Speech truncated by the video
  end: `</d>`.
- **On-screen text**: visible banners/signs/labels/subtitles/neon in
  English-style double quotation marks ("…" — quote style, not language),
  verbatim, no translation.
- **Per-shot content**: describe only what is visible or audible; establish
  composition, subject appearance and position, environment and lighting,
  actions and state changes, camera movement, current sound, and where
  referenced content appears. Never reduce to a plot summary or a list of
  reference relationships. ONE dominant action per shot.

## Section 5 — `overall_soundscape`

1–4 sentences, one paragraph: ambience, physical action sounds,
non-verbal human sounds across the FULL video. Do not repeat dialogue,
singing, or shot-synced sound events here. `N/A` only if the user explicitly
requests complete silence.

## Section 6 — `non_diegetic_music`

1–3 sentences about score the CHARACTERS CANNOT hear: instrumentation,
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
<Subject 1> 是一位年轻男子，脸部来自 <Picture 1>：深色短发、温暖的棕色眼睛、友好的圆脸。
<Subject 2> 是来自 <Picture 2> 的衣着：浅色牛仔夹克，内搭白色圆领 T 恤。

summary:
[reference generation] 目标视频展示 <Subject 1>，身穿 <Subject 2>，站在明亮现代的摄影棚里，带着温暖的笑容向镜头挥手致意。

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - 面部身份、深色短发与友好的表情完整保留。
<Subject 2> (appears in [Shot 1]): fully_preserved - 浅色牛仔夹克与白色 T 恤完整保留。

detailed_description:
目标视频采用写实摄影风格，影棚布光柔和平匀，背景为干净的浅灰色。
[Shot 1] 中景镜头框住 <Subject 1>——这位深色短发、圆脸友善的年轻男子来自 <Picture 1>，身穿来自 <Picture 2> 的浅色牛仔夹克与白色圆领 T 恤。他重心落在后脚，放松站立，面向镜头；随后抬起右手至肩高，手掌张开，缓慢地左右挥手致意，笑容随之展开。The camera holds a static shot for the first half of the clip, then pushes in with small amplitude at slow speed until the wave and the smile fill the frame.

overall_soundscape:
安静的摄影棚底噪，抬臂时牛仔夹克发出的轻微摩擦声，以及伴随笑容的一声轻短的呼气笑。

non_diegetic_music:
N/A
```
