from . import gemini, qwen_image


PROVIDERS = {
    "gemini": gemini.generate,
    "qwen_image": qwen_image.generate,
}

