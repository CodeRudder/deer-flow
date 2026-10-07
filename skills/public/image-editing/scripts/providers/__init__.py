from . import h3_i2i, openai_image_edit

PROVIDERS = {
    "openai_image_edit": openai_image_edit.edit,
    "h3_i2i": h3_i2i.edit,
}