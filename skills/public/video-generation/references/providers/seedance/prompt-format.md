# Seedance prompt format (doubao-seedance-2.x models)

Load this file when the routed provider is `seedance`, in place of the
MiniMax H3 formats. Follow the official Seedance 2.x prompt guide: a director
mindset, structured plain text with `@` material tags and a time/shot axis.
Never a raw JSON blob.

## Model family branch

The two generations organize time differently — branch on the model name:

- **Seedance 2.5** (`doubao-seedance-2-5-260628`) responds to integer-second
  timestamps. Long videos prefer `【阶段N】` (one state change + end state per
  stage) or continuous ranges `[0-10秒][10-20秒]`; also `第5s`, `3秒后`.
- **Seedance 2.0 family** (2.0 / fast / mini) is **unstable on exact
  timestamps** — use shot numbers only: `镜头1 / 镜头2 / 镜头3`. Do NOT use
  absolute seconds.

## Material tags and subject binding

Number every passed material by CLI argument order, starting at 1:

- `--reference-images a b` → `@image1` = a, `@image2` = b
- `--reference-videos v` → `@video1` = v
- `--reference-audios s` → `@audio1` = s

State each material's job in one clause (what to use, what not to):
`人物形象参考 @image1，不采用其背景`; `全程使用 @video1 的运镜方式`;
`使用 @audio1 的音色`. Give every passed material a role; unused materials
waste the reference budget.

Multi-subject scenes need explicit binding to avoid ID drift:
`将 @image1 中穿红色连衣裙的女人定义为<主体1>`, then refer to `<主体1>`
consistently. When a subject is undefined, bind `<主体N>@图片N` each time. Do
NOT put mapping info only inside an image (e.g. a name written on the picture)
— state mappings in text.

## Special characters

| Type | Symbol | Example |
|---|---|---|
| Music (BGM) | `（）` | `（背景中播放着快节奏的摇滚乐）` |
| SFX | `<>` | `<远处传来狗叫声>` |
| Dialogue | `{}` | `{你好，世界}`; non-Chinese needs a language tag: `用日语说道{こんにちは}` |
| Subtitle | `【】` | `【第一章：启程】` |

Put spoken lines in `{}` so they are voiced.

## Time / shot organization

### Shot budget (provider-independent)

Shared with the H3 format — use for shot planning and storyboard estimates:

| Duration | Shot count |
|---|---|
| 4–6 s | 1–2 shots |
| 7–10 s | 2–3 shots |
| 11–15 s | 3–5 shots |

### Seedance 2.5

Segment by seconds; one beat per ~5s is a good pace. Keep Chinese ≤500 chars
/ English ≤1000 words total.

```
<One-line intent: subject + event + style + audio direction.>

[0-10秒] <Shot 1: camera move + subject + action + environment.> {dialogue} <sfx>
[10-20秒] <Shot 2 …>
[20-30秒] <Shot 3 …>
```

Long narratives may use `【阶段1】…【阶段2】…` (one state change + end state per
stage). Relative time (`3秒后`, `第5s`) and time points also work. Keep the axis
continuous — avoid gaps like `0-3秒...5-6秒`.

### Seedance 2.0 family

Use shot numbers only (exact timestamps are unstable):

```
<One-line intent.>

镜头1：<camera move + subject + action>
镜头2：<...>
```

## Templates by mode

- **T2V**: one-line intent (subject + event + style + audio direction) +
  time/shot segments + closing constraints.
- **Reference (Ref2VA)**: material binding → overall intent → per-shot script
  referencing `@imageN/@videoN/@audioN` → consistency clause.
- **First frame / first+last**: `@image1 作为首帧。` as a standalone sentence;
  other materials declare "不改变首帧构图"; describe the motion between frames.

### Storyboard design images (Seedance keyframe reference)

The storyboard path (`references/storyboard.md`) passes one generated keyframe still
per shot; 2.0 family binds by shot numbers (its time/shot section). On 2.5
alignment is relatively strict (official keyframe reference); on 2.0 family
the stills read as ordinary references (weaker — note it on the plan card).

- **First sentence (mandatory, exact shape)**:
  `以图片 1 至图片 N 的顺序作为关键帧。` (N = storyboard image count; if
  original references ride along, keep the declaration scoped to the
  storyboard images' numbers, e.g. `以图片 1 至图片 3 的顺序作为关键帧。`)
- **Per-segment binding (mandatory)**: every time segment in the shot script
  must explicitly reference its OWN storyboard image —
  `[0-10秒] 段落以 @image1 的画面开始：…` — so each shot anchors to its still.
  A collapsed range note (`@image1至@imageN 分别对应镜头1至N 的构图`) alone
  does NOT satisfy this; the model aligns per reference, not per range.
  Unused materials waste the reference budget.
- Usage constraint sentence: `分镜关键帧仅参考构图与内容，不采用其画风与图内文字。`
- Ordering is load-bearing: label order = `--reference-images` order
  (storyboard images first by default; any attached originals follow in CLI
  order — the prompt numbering MUST match the CLI pass order).
- The keyframe declaration keeps the task a normal reference task — do NOT
  use frame-mode `--image-role` (mutually exclusive with reference mode), and
  the forbidden-words rule applies as usual (no 编辑/延长/续写…).
- Keyframe reference aligns composition relatively strictly but does not
  lock output params; ratio comes from `--aspect-ratio` as usual.

## Task-type locks (2.5 only — API Ref)

Seedance 2.5 locks output params for some task types. Violating fails
**asynchronously** (`InvalidParameter.TaskTypeConstraint`); the adapter
enforces them locally before submit. 2.0 family has no such locks.

| Task | Trigger | ratio | duration |
|---|---|---|---|
| First frame / first+last | `role=first_frame` / `last_frame` | forced `adaptive` | user `[4,30]` or `-1` (default `-1`) |
| Video edit | `reference_video` + edit keywords | forced `adaptive` | forced `-1` (input video 4–30s) |
| Video extend | `reference_video` + extend keywords | forced `adaptive` | user `[4,30]` or `-1` |
| T2V / reference | text / `reference_*` | user-set or `adaptive` | user `[4,30]` or `-1` |

Only `video-edit` locks `duration=-1`. Frame tasks lock `ratio` only — do NOT
promise a custom ratio in the plan card for 2.5 frame tasks. This workflow
always passes an explicit integer `--duration` (SKILL.md Output settings);
the `-1` values above are adapter capability, not workflow guidance.

## Forbidden words in reference mode (critical)

The API routes the task type from the prompt + `content.role`. In reference
mode (`--image-role reference`) NEVER use editing/extension intent words —
they flip the task into edit/extend mode, which locks `ratio=adaptive` (and
edit also forces `duration=-1`):

> 编辑、替换、换成、删掉、删除、去掉、增加、加上、修改、改成、延长、向前、向后、续写、延续

To describe continuous action, say it directly (`镜头继续跟随她走进门内`),
not "延长/续写". Genuine edit/extend requests are NOT supported yet (P1 — will
use `omni_reference_task_type=edit/extend` for synchronous validation) —
reroute them per SKILL.md instead of passing them through.

## Audio

- Audio is generated with the video by default (`generate_audio=true`); no
  extra cost. Set `false` for silent video.
- Dialogue in `{}` is voiced; non-Chinese needs a language tag (`使用英语`,
  `用日语说道{...}`). Multi-person scenes bind speaker per segment; others
  "自然闭口聆听".
- Describe ambient sound and music inline (轻脆的碰撞声、卡点鼓点、女生旁白…).
- Seedance 2.5 supports 11 dubbing languages; 2.0 family supports 6
  (zh/en/es/id/pt/ja).

## Constraints and negative control

Default constraint pack (append as needed):
- Subtitle guardrail: `保持无字幕` / `避免生成任何文字或字幕`.
- Watermark/Logo guardrail: `不要生成水印` / `不要生成Logo`.
- Multi-person twins drift: `禁止生成同款分身、双胞胎，同一画面仅保留单个对应人物`.
- Non-realistic style (anime/CG): anchor explicitly (`2D日漫风格`,
  `3D国风漫画`) — realistic reference images drift to live-action otherwise.
- Negative audio on demand: `无 bgm，只生成环境音和动作音` / `不要任何声音`.

## Action / camera norms

- One camera move per shot — do not stack push+pull+pan in one shot.
- Prefer slow, continuous small actions over explosive ones (狂奔/大跳/翻滚
  are unstable).
- Concretize emotions via body details (肩膀微微颤抖、眼眶泛红), not abstract
  words (很悲伤).
- Use deterministic verbs; avoid ambiguous double verbs (打飞).

## Difference from the H3 format

The H3 structured format (`[Shot N]` timeline, three fields / six sections,
`<d>` dialogue, audio-description block) is H3-only. Do NOT mix it with
Seedance's `@`-tag + `{}` dialogue + constraint pack. Pick one provider's
format per run.
