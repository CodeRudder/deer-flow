---
name: image-editing
description: Use this skill whenever an existing uploaded or generated image is the source that must be preserved or transformed, even if the user says "generate" or "create". Covers edits, retouching, replacement, recoloring, and structure-preserving transformations such as turning a design drawing, sketch, blueprint, CAD-style image, or product concept into a realistic physical-object image.
---

# Image Editing Skill

## Overview

This skill transforms an existing image by calling a provider-backed image edit script. It applies both to local edits and to larger visual transformations where the source image's structure, geometry, identity, or composition must remain recognizable.

## Core Capabilities

- Edit uploaded images or previously generated images
- Preserve composition, identity, pose, background, and unrelated details
- Apply local replacement or color/wardrobe/background changes
- Turn drawings, sketches, blueprints, design diagrams, or concept images into realistic product or physical-object images while preserving their structure
- Present edited output with `present_files`

## Workflow

### Step 1: Confirm this is an edit task

Use this skill only when the user is modifying an existing image. Do not use it for text-only image creation.

Route by the role of the input image, not by whether the user says "edit" or "generate":

- Use `image-editing` when the uploaded or previously generated image is the source to preserve, convert, materialize, or render more realistically.
- Use `image-generation` only when an image is optional inspiration and the user does not need its exact structure, geometry, layout, or identity preserved.
- An uploaded image plus a request such as "generate a real photo", "make this look like a real product", "render this design", or "turn this drawing into a physical object" is an image-editing task. The user does not need to explicitly say "edit this image".

Good fits:

- Change clothing, color, background, or a local object
- Keep the same subject and overall composition
- Refine a previously generated image
- Convert a part drawing, sheet-metal model, industrial design, sketch, or blueprint into a realistic physical-object image

Not a fit:

- Create a brand new image from scratch
- Reinterpret a text-only idea as a fresh illustration
- Ask the model to redraw the whole scene with no original image

### Step 2: Prepare the edit prompt

Write the prompt as editing instructions:

- Keep the user's original wording by default
- Only expand or optimize the prompt when the meaning is genuinely unclear
- If the user already described the change clearly, keep the prompt short and direct
- State what must change
- State what must stay the same
- Preserve composition, lighting, subject identity, background, and any other unspecified details unless the user says otherwise
- Add a default background instruction that the model should not alter parts the user did not mention
- Expand short prompts like "make it black" into a full edit instruction only when needed for clarity
- If the target is ambiguous, use `view_image` first. If it is still ambiguous after inspection, ask one clarifying question instead of guessing.

Default edit posture:

- Do not change anything the user did not explicitly mention
- Prefer minimal edits over broad restyling
- Keep the original scene stable unless the user asks for a larger change
- For design-to-real transformations, preserve geometry, proportions, bends, holes, edges, connections, and component layout unless the user explicitly asks to change them

Example background instruction:

- "Preserve the original composition and leave all unspecified details unchanged."

### Step 3: Execute the edit

Call the script with at least one input image and a unique output path:

```bash
python /mnt/skills/public/image-editing/scripts/edit.py \
  --image /mnt/user-data/uploads/input.png \
  --prompt "Change the shark to black and change the clothing to a tank top and shorts. Preserve the original composition and leave all unspecified details unchanged." \
  --output-file /mnt/user-data/outputs/edited-shark-and-clothes.png \
  --size auto \
  --quality high \
  --output-format png
```

Parameters:

- `--image`: Input image path, repeatable; use the actual file path from either `/mnt/user-data/uploads/` or `/mnt/user-data/outputs/`
- `--prompt`: Direct edit prompt text
- `--output-file`: Absolute path to the edited image output
- `--provider`: Optional provider override (`openai_image_edit` or `h3_i2i`); otherwise use `image_editing.default_provider`
- `--model`: Optional model override; otherwise use the provider default (see Providers below)
- `--size`: Optional provider size, default `auto`
- `--quality`: Optional provider quality, default `high`
- `--output-format`: Optional output format, default `png`
- `--mask`: Not supported yet; if passed, the script will fail explicitly

If multiple `--image` paths are provided, the first image is the primary edit target and the rest are auxiliary inputs or iterative references.

### Providers

`config.yaml` decides which providers exist and `image_editing.default_provider` picks the default; the script does not choose between them on its own.

| Provider | Model(s) | Credential | Structure preservation |
| --- | --- | --- | --- |
| `openai_image_edit` | `gpt-image-2` | `Authorization` in `config.yaml` | yes — honours "leave everything else unchanged" |
| `h3_i2i` | `h3-i2i-hq` (default), `h3-i2i-std` | none — builds its own `Bearer` from `H3_IMAGE_AUTH_TOKEN` | **no** — see the boundary below |

**The `h3_i2i` boundary is the part that matters.** It drives a self-hosted H3 gateway in
image-to-image mode: the reference image seeds a short clip as its first frame and a later
frame of that clip is returned — the image *evolved* by the prompt. That is generative
evolution / reference-guided redraw. It is good at "change the style / background / wardrobe
/ season". It does **not** promise that unedited regions stay pixel-identical, and it has no
mask or inpainting support.

So whenever `h3_i2i` is the provider in play — the default on a deployment without an OpenAI
credential, or selected explicitly — do **not** tell the user the untouched parts were
preserved, and do not promise a structure-preserving result for a design drawing, blueprint,
or CAD-style source. Describe what it actually does: a plausible variation guided by the
input image. If the request genuinely requires the source to survive, say plainly that no
provider on this deployment honours that contract, instead of quietly delivering a redraw.

Running `h3_i2i`:

- Exactly **one** reference image, passed as `--image`; PNG / JPEG / WebP, ≤12 MB. Zero, or
  more than one, fails before anything is sent to the gateway.
- `--model h3-i2i-hq` (default, ~50 s) or `--model h3-i2i-std` (~31 s). Any other model name
  is rejected: the text-to-image tiers (`h3-clip-*`, `h3-frame-*`) belong to
  `image-generation`, not here.
- `--size`, `--quality`, `--output-format`, and `--api-version` are accepted and ignored
  (the provider warns). The output is always a PNG at 1344×768.
- It shares one GPU and one serial queue with the video API and is **video-first**: while the
  engine runs a video job an image request yields rather than competing, so a call can take
  far longer than its tier suggests.
- If a call times out, or fails with `engine busy with video jobs`, that is the yield and not
  a broken request: **re-run the exact same command.** The provider derives an
  `Idempotency-Key` from the request, so the retry re-attaches to the job already queued
  instead of burning a second run on the shared GPU. Set `H3_IMAGE_NO_IDEMPOTENCY=1` only
  when you deliberately want a *different* result from the same prompt — otherwise the
  gateway's 24-hour same-key window returns the same image.

Environment variables for `h3_i2i`:

- `H3_IMAGE_AUTH_TOKEN` — Bearer token for the gateway (falls back to `H3IMG_AUTH_TOKEN`, the
  gateway's own variable name)
- `H3_IMAGE_BASE_URL` — gateway base URL (default: `http://100.108.144.120:8000`)
- `H3_IMAGE_MODEL` — default tier when `--model` is omitted (default: `h3-i2i-hq`)
- `H3_IMAGE_POLL_TIMEOUT_SECONDS` — poll budget (default: the config `timeout`, 540 s)
- `H3_IMAGE_POLL_INTERVAL_SECONDS` — poll interval (default 5 s)
- `H3_IMAGE_NO_IDEMPOTENCY` — set to `1` to force a brand-new job

## Prompt Examples

If the user already gave a clear request, pass it directly and add only the minimum missing constraints:

- `Change the shark to black and change the clothing to a tank top and shorts. Preserve the original composition, camera angle, background, lighting, identity, pose, and all unrelated details. Do not change anything the user did not explicitly mention.`
- `Make the background a warm sunset street scene. Keep the person, pose, framing, and clothing unchanged.`
- `Generate a realistic physical photo of this sheet-metal flat-bridge model. Preserve the exact structure, proportions, bends, holes, edges, and component layout shown in the uploaded design drawing. Use realistic metal materials, manufacturing details, lighting, and a neutral workshop background. Do not add or remove structural features.`

## Output Handling

- Save outputs under `/mnt/user-data/outputs/`
- Do not overwrite existing output files
- Use `present_files` to show the edited image to the user
- Only present the final edited file, not intermediate analysis or scratch output
