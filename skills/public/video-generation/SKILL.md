---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos. Supports structured prompts and reference image for guided generation.
---

# Video Generation Skill

## Overview

This skill generates high-quality videos using structured prompts and a Python script. The workflow includes creating JSON-formatted prompts and executing video generation with optional reference image.

## Core Capabilities

- Create structured JSON prompts for AIGC video generation
- Support reference image as guidance or the first/last frame of the video
- Generate videos through automated Python script execution

## Workflow

### Step 1: Understand Requirements

When a user requests video generation, identify:

- Subject/content: What should be in the image
- Style preferences: Art style, mood, color palette
- Technical specs: Aspect ratio, composition, lighting
- Reference image: Any image to guide generation
- You don't need to check the folder under `/mnt/user-data`

### Step 2: Create Structured Prompt

Generate a structured JSON file in `/mnt/user-data/workspace/` with naming pattern: `{descriptive-name}.json`

### Step 3: Create Reference Image (Optional when image-generation skill is available)

Generate reference image for the video generation.

- If only 1 image is provided, use it as the guided frame of the video

### Step 3: Execute Generation

Call the Python script:
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --reference-images /path/to/ref1.jpg \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --aspect-ratio 16:9
```

Parameters:

- `--prompt-file`: Absolute path to JSON prompt file (required)
- `--reference-images`: Absolute paths to reference image (optional); the first image is used as the first frame where supported
- `--output-file`: Absolute path to output video file (required)
- `--aspect-ratio`: Aspect ratio of the generated video (optional; applies to T2V, e.g. 16:9)
- `--provider`: Video provider (optional): `minimax_h3`, `gemini`, or `minimax_v1`
- `--model`: Provider model name (optional)
- `--resolution`: Output resolution where supported (optional), e.g. `768P` or `2K` for MiniMax H3
- `--duration`: Video length in seconds where supported (optional), e.g. 4-15 for MiniMax H3

[!NOTE]
Do NOT read the python file, instead just call it with the parameters.

## Video Generation Example

User request: "Generate a short video clip depicting the opening scene from "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe"

Step 1: Search for the opening scene of "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe" online

Step 2: Create a JSON prompt file with the following content:

```json
{
  "title": "The Chronicles of Narnia - Train Station Farewell",
  "background": {
    "description": "World War II evacuation scene at a crowded London train station. Steam and smoke fill the air as children are being sent to the countryside to escape the Blitz.",
    "era": "1940s wartime Britain",
    "location": "London railway station platform"
  },
  "characters": ["Mrs. Pevensie", "Lucy Pevensie"],
  "camera": {
    "type": "Close-up two-shot",
    "movement": "Static with subtle handheld movement",
    "angle": "Profile view, intimate framing",
    "focus": "Both faces in focus, background soft bokeh"
  },
  "dialogue": [
    {
      "character": "Mrs. Pevensie",
      "text": "You must be brave for me, darling. I'll come for you... I promise."
    },
    {
      "character": "Lucy Pevensie",
      "text": "I will be, mother. I promise."
    }
  ],
  "audio": [
    {
      "type": "Train whistle blows (signaling departure)",
      "volume": 1
    },
    {
      "type": "Strings swell emotionally, then fade",
      "volume": 0.5
    },
    {
      "type": "Ambient sound of the train station",
      "volume": 0.5
    }
  ]
}
```

Step 3: Use the image-generation skill to generate the reference image

Load the image-generation skill and generate a single reference image `narnia-farewell-scene-01.jpg` according to the skill.

Step 4: Use the generate.py script to generate the video
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/narnia-farewell-scene.json \
  --reference-images /mnt/user-data/outputs/narnia-farewell-scene-01.jpg \
  --output-file /mnt/user-data/outputs/narnia-farewell-scene-01.mp4 \
  --aspect-ratio 16:9
```
> Do NOT read the python file, just call it with the parameters.

## Output Handling

After generation:

- Videos are typically saved in `/mnt/user-data/outputs/`
- Share generated videos (come first) with user as well as generated image if applicable, using `present_files` tool
- Provide brief description of the generation result
- Offer to iterate if adjustments needed

## Notes

- Always use English for prompts regardless of user's language
- JSON format ensures structured, parsable prompts
- Reference image enhance generation quality significantly
- Iterative refinement is normal for optimal results

## Providers

Provider is resolved in this order: `--provider` CLI flag > `VIDEO_GENERATION_PROVIDER`
env > first provider in `config.yaml` `video_generation.providers[]` > credential
fallback (`GEMINI_API_KEY` → `gemini`, else `MINIMAX_VIDEO_API_KEY` → `minimax_h3`,
else shared `MINIMAX_API_KEY` → `minimax_v1`).

- `minimax_h3` — MiniMax H3 via the V2 API (recommended). 768P/2K, 4-15s, native
  stereo audio. T2V honors `--aspect-ratio`; for I2V the first reference image is
  sent as the first frame and the ratio follows that image. Env:
  `MINIMAX_VIDEO_API_KEY` (preferred) or the shared `MINIMAX_API_KEY`; optional
  `MINIMAX_API_HOST` (default `https://api.minimaxi.com`).
- `gemini` — Google Veo (`x-goog-api-key` auth); reference images are passed as
  asset images. Env: `GEMINI_API_KEY`.
- `minimax_v1` — legacy Hailuo V1 (compatibility only). The old provider name
  `minimax` is an alias for it, so `VIDEO_GENERATION_PROVIDER=minimax` keeps the
  old behavior. Env: same credential resolution as `minimax_h3`; optional
  `MINIMAX_VIDEO_MODEL` (default `MiniMax-Hailuo-2.3`).

Params a provider does not support print a warning instead of being dropped silently.
