# Seedance prompt format (all doubao-seedance-2-x models)

Load this file when the routed provider is `seedance`, in place of the
MiniMax H3 formats. Follow the official Seedance 2.x prompt guide: a timeline
of shots with explicit material tags. Structured plain text only — never a raw
JSON blob.

## Core structure

```
<One-line overall intent: subject, product/story, style, and audio direction.>

[0-5秒] <Shot 1: subject + action + camera + lighting.> 台词或音效：「…」
[5-12秒] <Shot 2 …>
[12-15秒] <Shot 3 …>
```

- Segment the video by seconds (`[0-10秒][10-20秒][10-30秒]` style). Segment
  count follows the target duration; one beat per ~5 seconds is a good pace.
- Lead each shot with the shot type / camera move (特写、全景、跟拍、环绕、
  推近、拉远、固定机位、手持晃动…), then subject, action, and environment.
- Keep Chinese ≤500 characters / English ≤1000 words total.

## Material tags (@image1 / @video1 / @audio1)

When reference materials are passed, refer to them by explicit tag. Tag
numbers match the CLI argument order, starting at 1:

- `--reference-images a b` → `@image1` = a, `@image2` = b
- `--reference-videos v` → `@video1` = v
- `--reference-audios s` → `@audio1` = s

State each material's job in one clause: `全程使用 @video1 的运镜方式`,
`人物形象参考 @image1`, `全程使用 @audio1 作为背景音乐`. Give every passed
material a role; unused materials waste the reference budget.

## Audio

- Audio is generated with the video by default (no extra cost).
- Put spoken lines in quotes so they are voiced: 男人说：「你记住，以后不可以用手指指月亮。」
- Describe ambient sound and music direction inline (轻脆的碰撞声、卡点鼓点、
  女生旁白…); Seedance 2.5 supports 11 dubbing languages.

## Forbidden words in reference mode (critical)

The API routes the task type from the prompt. In reference mode
(`--image-role reference`) NEVER use editing/extension intent words — they
flip the task into video-edit/extend mode, which locks the output to the
source's ratio/duration and fails the task asynchronously when combined with a
custom ratio/duration:

> 编辑、替换、换成、删掉、去掉、增加、修改、延长、向前、向后、续写、延续

To *describe continuous action*, say it directly (`镜头继续跟随她走进门内`),
not "延长/续写". Genuine edit/extend requests are NOT supported yet (P1) —
reroute them per SKILL.md instead of passing them through.

## Frame modes

`first_frame` / `last_frame` / `first_last` need no special tags — the frame
images are bound by role. The prompt describes the motion between frames and,
for `first_last`, the transition. On Seedance 2.5 the output follows the
frames (adaptive ratio, auto duration), so do not promise a custom ratio or
duration in the plan card.
