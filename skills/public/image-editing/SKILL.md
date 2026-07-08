---
name: image-editing
description: Use this skill when the user wants to modify, edit, retouch, replace, remove, recolor, or partially transform an existing image while preserving unrelated parts of the original image.
---

# Image Editing Skill

## Overview

This skill edits an existing image in place by calling a provider-backed image edit script. It is for cases where the user wants to keep the original composition or subject and change only selected parts.

## Core Capabilities

- Edit uploaded images or previously generated images
- Preserve composition, identity, pose, background, and unrelated details
- Apply local replacement or color/wardrobe/background changes
- Present edited output with `present_files`

## Workflow

### Step 1: Confirm this is an edit task

Use this skill only when the user is modifying an existing image. Do not use it for text-only image creation.

Good fits:

- Change clothing, color, background, or a local object
- Keep the same subject and overall composition
- Refine a previously generated image

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

## Output Handling

- Save outputs under `/mnt/user-data/outputs/`
- Do not overwrite existing output files
- Use `present_files` to show the edited image to the user
- Only present the final edited file, not intermediate analysis or scratch output
