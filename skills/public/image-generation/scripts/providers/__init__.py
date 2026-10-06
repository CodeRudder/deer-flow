from . import h3_image, openai_image, qwen_image


PROVIDERS = {
    "h3_image": h3_image.generate,  # self-hosted H3 frame-extraction gateway
    "openai_image": openai_image.generate,
    "qwen_image": qwen_image.generate,
}
