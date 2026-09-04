from . import minimax_h3, minimax_v1, seedance


# name -> provider class. Unlike image providers (which register a plain
# generate function), video providers register the BaseVideoProvider subclass
# so generate.py can bind the model then run the three-step template:
#   PROVIDERS[name](model=...).generate(...)
PROVIDERS = {
    "minimax_h3": minimax_h3.PROVIDER,  # V2, recommended
    "minimax_v1": minimax_v1.PROVIDER,  # legacy Hailuo V1, compat only
    "seedance": seedance.PROVIDER,  # Volcano Ark doubao-seedance-2.x family
}

# name -> capability manifest (single source of truth for capability data;
# consumed by check_materials.py and --describe-provider rendering).
MANIFESTS = {
    "minimax_h3": minimax_h3.PROVIDER_MANIFEST,
    "minimax_v1": minimax_v1.PROVIDER_MANIFEST,
    "seedance": seedance.PROVIDER_MANIFEST,
}

# name -> image roles the adapter accepts (consumed by --describe-provider
# rendering; seedance has no single-tail-frame mode — see its _build_content).
IMAGE_ROLES = {
    "minimax_h3": minimax_h3.IMAGE_ROLES,
    "minimax_v1": minimax_v1.IMAGE_ROLES,
    "seedance": seedance.IMAGE_ROLES,
}

# Model name -> provider name, covering every model an adapter serves (not just
# its default). When config.yaml has no video_generation section this is the
# model->provider truth for credential-fallback routing; an unknown model is
# rejected there rather than paired with whichever credential happens to be set.
MODEL_PROVIDERS = {model: name for name, cls in PROVIDERS.items() for model in (cls.known_models or (cls.default_model,))}
