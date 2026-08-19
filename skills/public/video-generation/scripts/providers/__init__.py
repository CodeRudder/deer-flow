from . import gemini, minimax_h3, minimax_v1


# name -> provider class. Unlike image providers (which register a plain
# generate function), video providers register the BaseVideoProvider subclass
# so generate.py can bind the model then run the three-step template:
#   PROVIDERS[name](model=...).generate(...)
PROVIDERS = {
    "minimax_h3": minimax_h3.PROVIDER,  # V2, recommended
    "gemini": gemini.PROVIDER,
    "minimax_v1": minimax_v1.PROVIDER,  # legacy Hailuo V1, compat only
}

# Model name -> provider name, covering every model an adapter serves (not just
# its default). When config.yaml has no video_generation section this is the
# model->provider truth for credential-fallback routing; an unknown model is
# rejected there rather than paired with whichever credential happens to be set.
MODEL_PROVIDERS = {model: name for name, cls in PROVIDERS.items() for model in (cls.known_models or (cls.default_model,))}
