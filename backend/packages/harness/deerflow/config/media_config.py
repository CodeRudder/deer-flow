"""Configuration for media-related platform behaviors."""

from pydantic import BaseModel, Field


class MediaConfig(BaseModel):
    """Media delivery configuration.

    Currently hosts the artifact auto-present safety net: when an agent run
    writes new files into the thread outputs directory but forgets to call
    ``present_files``, the platform registers them into ``artifacts`` anyway
    so the client can render them.
    """

    auto_present_outputs: bool = Field(
        default=True,
        description=("Automatically present new files written to the thread outputs directory at run end (safety net for agents that finish without calling present_files). Idempotent: files already in artifacts are never duplicated."),
    )
