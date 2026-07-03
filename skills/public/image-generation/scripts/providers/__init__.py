from . import openai_image, qwen_image


PROVIDERS = {
    "openai_image": openai_image.generate,
    "qwen_image": qwen_image.generate,
}
