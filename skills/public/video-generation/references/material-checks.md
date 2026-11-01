# Material checks (素材检查细则)

Loaded from SKILL.md at material-check time — after the mode is routed and
the images (user-supplied or collected) are in hand, BEFORE prompt writing
or any image spend. This file owns the per-mode image counts, the check
list, and the ratio-conflict exits.

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
  `references/workarounds.md` — after material checks and before prompt
  writing or image generation. Never redraw silently; MiniMax H3 needs no
  workaround.

Do not require matching ratios across `reference` images, and do not compare
across images for a single first or last frame. If frame-image ratios conflict
with each other or with an explicitly requested output ratio, use exactly
three exits: ① crop the frame image(s) to the requested or a common ratio
② replace the conflicting image(s) ③ keep the original frame-image ratios —
a single frame keeps the image's native ratio; `first_last` keeps both and
explicitly accepts the transition mismatch. An accepted exception counts as a
passed material check and is never asked again.
