from pydantic import BaseModel, Field


class SuggestionsConfig(BaseModel):
    """Configuration for automatic follow-up suggestions."""

    enabled: bool = Field(default=True, description="Whether to enable follow-up question suggestions at the end of an AI response")
    model_name: str | None = Field(
        default=None,
        description="Model used to generate suggestions; null falls back to the first configured model (models[0]).",
    )
