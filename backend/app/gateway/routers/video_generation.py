from fastapi import APIRouter

from deerflow.video_generation import VideoGenerationProvidersResponse, get_video_generation_providers

router = APIRouter(prefix="/api/video-generation", tags=["video-generation"])


@router.get(
    "/providers",
    response_model=VideoGenerationProvidersResponse,
    summary="List Video Generation Providers",
    description="Retrieve video generation providers exposed by the built-in video-generation skill.",
)
async def list_video_generation_providers() -> VideoGenerationProvidersResponse:
    return get_video_generation_providers()
