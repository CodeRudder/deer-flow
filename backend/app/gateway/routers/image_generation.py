from fastapi import APIRouter

from deerflow.image_generation import ImageGenerationProvidersResponse, get_image_generation_providers

router = APIRouter(prefix="/api/image-generation", tags=["image-generation"])


@router.get(
    "/providers",
    response_model=ImageGenerationProvidersResponse,
    summary="List Image Generation Providers",
    description="Retrieve image generation providers exposed by the built-in image-generation skill.",
)
async def list_image_generation_providers() -> ImageGenerationProvidersResponse:
    return get_image_generation_providers()
