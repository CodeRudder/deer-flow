from . import gemini, minimax_h3, minimax_v1


# name -> provider class. Unlike image providers (which register a plain
# generate function), video providers register the BaseVideoProvider subclass
# so generate.py can bind the model then run the three-step template:
#   PROVIDERS[name](model=...).generate(...)
PROVIDERS = {
    "minimax_h3": minimax_h3.PROVIDER,  # V2, recommended
    "gemini": gemini.PROVIDER,  # preserved, not regressed
    "minimax_v1": minimax_v1.PROVIDER,  # legacy Hailuo V1, compat only
}

# Default model -> provider name. Mirrors the harness builtin table; when
# config.yaml has no video_generation section this is the model->provider
# truth for credential-fallback routing.
MODEL_PROVIDERS = {cls.default_model: name for name, cls in PROVIDERS.items()}
