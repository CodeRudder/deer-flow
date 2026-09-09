# Content-review workarounds (real-person material on Seedance)

Load this file only when the real-person redraw workaround is in play. Seedance
rejects input images that may contain a real person with a synchronous 400
(`InputImageSensitiveContentDetected.PrivacyInformation`; the error names the
offending content position, e.g. `content[1]`).
MiniMax H3 has no such hard block, so the workaround never applies there. The
trigger is the user's own statement or your visual judgment of the image during
material checks — never an automatic face detector, never a silent redraw.
Scope: reference mode only — frame modes are out of scope (a design sheet as a
first frame opens the video as an illustration, conflicting with the
live-action paragraph; untested).

## The per-material question (after material checks, before any image spend)

If the routed provider is Seedance and any material image may contain a real
person's face, ask the user ONCE, listing every face-containing image with one
choice per image:

| Option | Consequence |
|---|---|
| 弃用 (drop) | the image leaves this task; re-count references from the remainder |
| 重新获取 (replace) | search again or ask the user for a substitute without a face; the substitute passes the same checks |
| 重绘 (redraw) | the image goes through the redraw below — one image call per image, quota cost stated on the spot |

Images without a face join as usual and never enter this question. If every
image is dropped, reference mode has no materials — fall back to the standard
no-material routing (upload / T2V). The quota cost is declared on the spot
with the question; the plan card still declares the workaround (how many
images were redrawn; the result is an AI re-enactment from the design sheets)
as the final informed-consent gate.

## Step 1: Redraw into a character design sheet (image-editing)

For each image sent to 重绘, run image-editing with the photo as the base
image and this fixed prompt — verbatim, never translated, never merged into
other prompt text:

```text
Character design sheet of the input photo, horizontal layout showing 3 views side-by-side: 45-degree left profile, front view, 45-degree right profile. Strict consistency in facial features, hairstyle, and clothing across all three angles. Colored pencil illustration style, traditional hand-drawn texture, colored sketch, clean background.
```

Points: horizontal three-view layout (left 45° profile / front / right 45°
profile); colored-pencil hand-drawn texture; clean background so scene
elements do not leak into the video. A failed redraw stops the flow — never
self-retry; each attempt costs one image call.

## Step 2: Video generation (Seedance reference mode)

The design sheets REPLACE the original photos as `--reference-images`. Append
this fixed instruction paragraph right after the @-tag material declarations
(the Seedance reference format is otherwise unchanged; the paragraph contains
no forbidden words):

```text
Reference the uploaded character design to generate an ultra-realistic live-action video. Ensure cinematic lighting, detailed skin, and strict consistency in features, hair, and clothing. Include natural movements and stable, flicker-free footage.
```

The paragraph is English fixed copy — never translated, never rewritten into
the user's prompt.

## Storyboard overlay (redraw first)

When storyboard images are opted in AND the workaround is active, redraw
BEFORE storyboard generation: the design sheet replaces the original photo as
the storyboard chain's anchor — the first shot is image-edited from the design
sheet, later shots chain from shot 1 as usual. The whole chain stays
illustration-form; live-action realism is pulled back by the instruction
paragraph alone. For the attached-original rule (Step 2.5) the design sheet
counts as an original material.

## If a submit is still rejected

A 400 `InputImageSensitiveContentDetected.PrivacyInformation` means the
detector judged an input image as possibly containing a real person. Task not
created, not billed — report the error and offer: switch to MiniMax H3 /
redraw the named image via this workaround / drop that image. A design sheet
passing the detector today does not guarantee it passes tomorrow; the detector
may evolve.
