from . import gemini, openai_image, qwen_image


PROVIDERS = {
    "gemini": gemini.generate,
    "openai_image": openai_image.generate,
    "qwen_image": qwen_image.generate,
}
