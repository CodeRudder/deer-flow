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
- `--provider`: Optional provider override; otherwise use `image_editing.default_provider`
- `--model`: Optional model override; otherwise use the provider default
- `--size`: Optional provider size, default `auto`
- `--quality`: Optional provider quality, default `high`
- `--output-format`: Optional output format, default `png`
- `--mask`: Not supported yet; if passed, the script will fail explicitly

If multiple `--image` paths are provided, the first image is the primary edit target and the rest are auxiliary inputs or iterative references.

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
