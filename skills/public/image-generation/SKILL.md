---
name: image-generation
description: Use this skill to create a new image from text, or when reference images are only loose inspiration for style or composition. Do not use it when an uploaded or previously generated image is the source whose structure, geometry, layout, identity, or composition must be preserved or transformed; use image-editing for those cases, including design drawing, sketch, blueprint, or CAD-style image to realistic product/photo requests even when the user says "generate".
---

# Image Generation Skill

## Overview

This skill generates high-quality images from the user's prompt using a Python script. The workflow includes creating a JSON prompt file that preserves the user's original image prompt and executing image generation with optional reference images.

## Core Capabilities

- Create JSON prompt files for AIGC image generation
- Preserve the user's original image prompt by default
- Support multiple reference images for style/composition guidance
- Generate images through automated Python script execution
- Handle various image generation scenarios (character design, scenes, products, etc.)

## Workflow

### Step 1: Understand Requirements

When a user requests image generation, identify:

- Original image prompt: The exact text the user wants the image model to follow
- Explicit style preferences: Art style, mood, color palette
- Explicit technical specs: Aspect ratio, composition, lighting
- Reference images: Any images to guide generation
- You don't need to check the folder under `/mnt/user-data`

Routing rule:

- Use `image-generation` when creating a new image from text or using references only as loose inspiration.
- Use `image-editing` when the user uploaded an image and expects the result to preserve or transform that image's structure, geometry, layout, identity, or composition.
- Requests such as "这是零件设计图，帮我生成实物图" are image-editing tasks because the design drawing is the source of truth, even though the user says "生成".
- **A reference image alone does not move the task to `image-editing`.** The deciding
  question is whether the unedited regions must stay intact. "换风格 / 换背景 / 姿态推演" on an
  uploaded photo, where the user is content to let the model redraw, is generative
  evolution → stay in this workflow and use an `h3-i2i-*` model (see Image-to-image below).
- If the request genuinely belongs to `image-editing` but that skill is disabled or its
  provider is unavailable, do **not** keep retrying it — say so, and offer the closest
  `image-generation` alternative (an `h3-i2i-*` model) with its limitation spelled out.

### Step 2: Create Prompt JSON

Generate a JSON file in `/mnt/user-data/workspace/` with naming pattern: `{descriptive-name}.json`.

The `prompt` field is the authoritative image prompt. By default, copy the user's original image prompt verbatim into `prompt`.

Do not translate, rewrite, summarize, expand, or infer missing visual details unless the user explicitly asks for prompt optimization, translation, rewriting, or enrichment.

For follow-up modification requests on an uploaded or previously generated image, decide by
what must survive. "turn this design into a real product photo" or "recolor this part but
keep the rest identical" need the source preserved → use the `image-editing` skill instead. But
"make it red", "change the background", or "adjust the previous image" where the user is
content to let the model redraw is generative evolution → stay in this workflow, pass the
image via `--reference-images`, and select an `h3-i2i-*` model (see Image-to-image below).

Use this default shape:

```json
{
  "prompt": "the user's original image prompt, copied verbatim"
}
```

Optional fields such as `negative_prompt`, `style`, `composition`, `lighting`, `color_palette`, and `technical` may be included only when the user explicitly provided those requirements. These fields must not override, contradict, or replace `prompt`.

When optional fields are useful, keep `prompt` verbatim and add only explicit metadata:

```json
{
  "prompt": "the user's original image prompt, copied verbatim",
  "negative_prompt": "only if explicitly provided by the user",
  "style": "only if explicitly provided by the user",
  "composition": "only if explicitly provided by the user",
  "lighting": "only if explicitly provided by the user",
  "color_palette": "only if explicitly provided by the user",
  "technical": {
    "aspect_ratio": "only if explicitly provided by the user or needed for the command"
  }
}
```

### Step 3: Execute Generation

Choose a unique output filename before executing generation. Never reuse an existing file in `/mnt/user-data/outputs/`, because previous conversation messages may still reference that path. Use a stable descriptive prefix plus a unique suffix such as `{descriptive-name}-{YYYYMMDD-HHMMSS}-{short-id}.png` or `.jpg`. If the intended output path already exists, choose a different filename instead of overwriting it.

Call the Python script:
```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --reference-images /path/to/ref1.jpg /path/to/ref2.png \
  --output-file /mnt/user-data/outputs/generated-image.jpg
  --aspect-ratio 16:9
```

Parameters:

- `--prompt-file`: Absolute path to JSON prompt file (required)
- `--reference-images`: Absolute paths to reference images (optional, space-separated)
- `--output-file`: Absolute path to output image file (required)
- `--aspect-ratio`: Aspect ratio of the generated image (optional, default: 16:9)
- `--model`: Model name, e.g. `qwen-image-2.0-pro` (optional). This is the routing key — the provider that owns the model is looked up from `config.yaml`, so normally this is the only one you pass.
- `--provider`: Escape hatch (`qwen_image`, `openai_image`, or `h3_image`) for debugging or for a model not declared in `config.yaml`; skips the model lookup (optional)
- `--negative-prompt`: Negative prompt for providers that support it (optional)
- `--prompt-extend`: Whether the provider should extend the prompt, `true` or `false` (optional)
- `--watermark`: Whether the provider should add a watermark, `true` or `false` (optional)

Provider can also be configured with environment variables:

- `IMAGE_GENERATION_PROVIDER`: override provider, e.g. `qwen_image` or `openai_image`
- `IMAGE_GENERATION_MODEL`: override provider model
- `QWEN_IMAGE_API_KEY`: Bearer token for Qwen-Image
- `QWEN_IMAGE_BASE_URL`: Qwen API base URL (optional, default: `https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1`)
- `H3_IMAGE_AUTH_TOKEN`: Bearer token for the self-hosted H3 image gateway (falls back to `H3IMG_AUTH_TOKEN`, the gateway's own variable name)
- `H3_IMAGE_BASE_URL`: H3 image gateway base URL (optional, default: `http://100.108.144.120:8000`)
- `H3_IMAGE_MODEL`: default gateway mode when `--model` is omitted (optional, default: `h3-clip-std`)
- `H3_IMAGE_SEED`: fix the sampling seed for a reproducible image (optional; the actual seed is echoed back in the result, so a good take can be re-derived)
- `H3_IMAGE_FRAME_POLICY`: which frame of the clip to grab — `first`, `last`, `middle`, or `at:<0..1>` (optional; unset leaves the tier's own default, which is `first` for text-to-image and `last` for the `h3-i2i-*` tiers)
- `H3_IMAGE_REFERENCE_FRAME_INDEX`: where the reference image seeds the clip — `0` (first frame, the default) or `-1` (last frame). Only meaningful for the `h3-i2i-*` tiers (optional)
- `H3_IMAGE_SHORT_EDGE`: override the tier's short edge, 128–2048 (optional; e.g. `512` on the `std` tier ≈ 50 s)
- `H3_IMAGE_NO_IDEMPOTENCY`: set to `1` to force a brand-new job — needed only for a deliberate re-roll (see below)

`h3_image` wraps a self-hosted H3 gateway that renders a short clip and returns a grabbed
frame as PNG. Its model names ARE the gateway's quality tiers, in two families — **the
`h3-clip-*` tiers lead and are the defaults**: they render only the shortest decodable
fragment (22 frames ≈ 0.92 s) and produce the same 768p frame as the `h3-frame-*` tiers at
a fraction of the cost. In menu order (the first is what the picker selects by default):
`h3-clip-std` (768p, ~25 s, the default) for normal work, `h3-clip-hq` (768p, ~40 s) for
the final image, then `h3-frame-draft` (256p, ~9 s) and `h3-frame-fast` (256p, ~16 s) to
iterate cheaply on a prompt, and finally the older `h3-frame-std` (~115 s) and
`h3-frame-hq` (~225 s) — same frames but 5–9× slower, kept only when `frame_policy` must
range across a wider 4-second span. It shares one GPU and one serial queue with the video
API and is **video-first**: while the engine runs a video job an image request yields
instead of competing for the slot, so a call can take far longer than its tier suggests. It
ignores `--negative-prompt` / `--prompt-extend` / `--watermark`.

#### Which model applies

The model name **is** the gateway mode; pass it as `--model`. There is no capability probe in
this skill — `--describe-provider` belongs to the **video-generation** skill and does not
exist here, so the table below is the authoritative list.

| Model | Use it for | Reference image | Output | ≈Time |
| --- | --- | --- | --- | --- |
| `h3-clip-std` | text-to-image, everyday (default) | not accepted | 1344×768 | 25 s |
| `h3-clip-hq` | text-to-image, final take | not accepted | 1344×768 | 40 s |
| `h3-frame-draft` | text-to-image, prompt iteration | not accepted | 448×256 | 9 s |
| `h3-frame-fast` | text-to-image, cheap draft | not accepted | 448×256 | 16 s |
| `h3-frame-std` | text-to-image, legacy | not accepted | 1344×768 | 115 s |
| `h3-frame-hq` | text-to-image, legacy final | not accepted | 1344×768 | 225 s |
| **`h3-i2i-std`** | **image-to-image, everyday** | **required, exactly 1** | 1344×768 | 31 s |
| **`h3-i2i-hq`** | **image-to-image, final take** | **required, exactly 1** | 1344×768 | 50 s |

#### Image-to-image runs

Use these whenever the user supplies a reference image as a **starting point to evolve** —
changing style, background, or pose while keeping the subject recognisable. Do **not** pair a
reference image with any `h3-clip-*` / `h3-frame-*` model: those are text-to-image and the
call fails rather than ignoring the image.

- Exactly **one** reference image, PNG / JPEG / WebP, ≤12 MB. Two images, a non-image file,
  or a missing path all fail before anything reaches the gateway.
- The reference seeds the clip as its **first frame** and the tier then grabs the **last**
  frame — the evolved end state, which is normally what the user wants. Set
  `H3_IMAGE_FRAME_POLICY=first` to get a near-copy instead.
- `H3_IMAGE_REFERENCE_FRAME_INDEX=-1` seeds the clip at the **last** frame rather than the
  first (`0`, the default).

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/fox-summer.json \
  --reference-images /mnt/user-data/uploads/base.png \
  --output-file /mnt/user-data/outputs/fox-summer-{YYYYMMDD-HHMMSS}.png \
  --aspect-ratio 16:9 \
  --model h3-i2i-hq
```

Boundary: these tiers do **generative evolution / reference-guided redraw**. They do **not**
promise that unedited regions stay pixel-identical. When the source must be *preserved* — a
design drawing, sketch, or blueprint turned into a product photo, where structure and identity
have to survive — that is the `image-editing` skill's contract, not this one.

If an `h3_image` call times out, or fails with `engine busy with video jobs`, that is the
yield and not a broken request: **re-run the exact same command.** The provider derives an
`Idempotency-Key` from the request, so the retry re-attaches to the job already queued
instead of burning a second run on the shared GPU. Set `H3_IMAGE_NO_IDEMPOTENCY=1` only
when you deliberately want a *different* image from the same prompt — otherwise the
gateway's 24-hour same-key window would hand back the same one.

Non-secret defaults can be configured in `config.yaml`:

```yaml
image_generation:
  providers:
    - name: qwen_image
      models:
        - qwen-image-2.0-pro
```

The configured provider list in `config.yaml` controls which providers are selectable. Provider-specific defaults such as model, base URL, timeout, prompt extension, and watermark live in the provider implementation and can be overridden with CLI arguments or environment variables when needed.

Priority: CLI arguments > environment variables > `config.yaml` provider > provider built-in defaults.

[!NOTE]
Do NOT read the python file, just call it with the parameters.

## Character Generation Example

User request: "Create a Tokyo street style woman character in 1990s"

Create prompt file: `/mnt/user-data/workspace/asian-woman.json`
```json
{
  "prompt": "Create a Tokyo street style woman character in 1990s"
}
```

Execute generation:
```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/asian-woman.json \
  --output-file /mnt/user-data/outputs/asian-woman-01.jpg \
  --aspect-ratio 2:3
```

Using Qwen-Image:
```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --provider qwen_image \
  --model qwen-image-2.0-pro \
  --prompt-file /mnt/user-data/workspace/asian-woman.json \
  --output-file /mnt/user-data/outputs/asian-woman-01.png \
  --aspect-ratio 1:1 \
  --watermark false
```

Qwen-Image currently supports text-to-image only in this skill. Do not pass `--reference-images` when using `--provider qwen_image`.

If Qwen-Image returns `DataInspectionFailed` or `Green net check failed for input text`, the prompt did not pass provider safety inspection. Rewrite the prompt with more neutral wording and remove sensitive entities, real people, political, military, national-security, violent, sexual, or other restricted content before retrying.

With reference images:
```json
{
  "prompt": "Character inspired by [Image 1] standing next to a vehicle inspired by [Image 2] on a bustling alien planet street in Star Wars universe aesthetic.",
  "technical": {
    "aspect_ratio": "9:16",
    "quality": "high",
    "detail_level": "highly detailed with film-like texture"
  }
}
```
```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/star-wars-scene.json \
  --reference-images /mnt/user-data/uploads/character-ref.jpg /mnt/user-data/uploads/vehicle-ref.jpg \
  --output-file /mnt/user-data/outputs/star-wars-scene-01.jpg \
  --aspect-ratio 16:9
```

## Common Scenarios

The `prompt` field remains the source of truth across all scenarios. Add optional metadata only when the user explicitly provided it.

**Character Design**:
- Physical attributes (gender, age, ethnicity, body type)
- Facial features and expressions
- Clothing and accessories
- Historical era or setting
- Pose and context

**Scene Generation**:
- Environment description
- Time of day, weather
- Mood and atmosphere
- Focal points and composition

**Product Visualization**:
- Product details and materials
- Lighting setup
- Background and context
- Presentation angle

## Specific Templates

Read the following template file only when matching the user request.

- [Doraemon Comic](templates/doraemon.md)

## Output Handling

After generation:

- Images are typically saved in `/mnt/user-data/outputs/`
- Share generated images with user using `present_files` tool
- Do NOT call `view_image` for generated output images. `view_image` is only for model-side visual inspection when image analysis is explicitly needed, not for presenting generated images to the user.
- Provide brief description of the generation result
- Offer to iterate if adjustments needed

## Tips: Enhancing Generation with Reference Images

For scenarios where visual accuracy is critical, **use the `image_search` tool first** to find reference images before generation.

**Recommended scenarios for using image_search tool:**
- **Character/Portrait Generation**: Search for similar poses, expressions, or styles to guide facial features and body proportions
- **Specific Objects or Products**: Find reference images of real objects to ensure accurate representation
- **Architectural or Environmental Scenes**: Search for location references to capture authentic details
- **Fashion and Clothing**: Find style references to ensure accurate garment details and styling

**Example workflow:**
1. Call the `image_search` tool to find suitable reference images:
   ```
   image_search(query="Japanese woman street photography 1990s", size="Large")
   ```
2. Download the returned image URLs to local files
3. Use the downloaded images as `--reference-images` parameter in the generation script

This approach significantly improves generation quality by providing the model with concrete visual guidance rather than relying solely on text descriptions.

## Notes

- Keep the user's original prompt language and wording by default
- JSON format ensures structured, parsable prompts while preserving the original `prompt`
- Reference images enhance generation quality significantly
- Iterative refinement is normal for optimal results
- For character generation, include optional character metadata only when the user explicitly provided those details; never replace the original `prompt`
