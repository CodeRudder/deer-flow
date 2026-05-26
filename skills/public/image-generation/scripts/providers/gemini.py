import base64
import os

import requests


DEFAULT_MODEL = "gemini-3-pro-image-preview"


def generate(
    *,
    prompt_text: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
    model: str | None = None,
    negative_prompt: str | None = None,
    prompt_extend: bool | None = None,
    watermark: bool | None = None,
) -> str:
    parts = []

    for reference_image in reference_images:
        with open(reference_image, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        parts.append(
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": image_b64,
                }
            }
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "GEMINI_API_KEY is not set"

    selected_model = model or DEFAULT_MODEL
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent",
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
            "contents": [{"parts": [*parts, {"text": prompt_text}]}],
        },
    )
    response.raise_for_status()
    json = response.json()
    response_parts: list[dict] = json["candidates"][0]["content"]["parts"]
    image_parts = [part for part in response_parts if part.get("inlineData", False)]
    if len(image_parts) == 1:
        base64_image = image_parts[0]["inlineData"]["data"]
        with open(output_file, "wb") as f:
            f.write(base64.b64decode(base64_image))
        return f"Successfully generated image to {output_file}"
    raise Exception("Failed to generate image")
