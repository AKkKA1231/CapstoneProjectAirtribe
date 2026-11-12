"""OpenAI-compatible model-list response schemas."""

from typing import Literal

from pydantic import BaseModel


class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: Literal["prism"] = "prism"


class ModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelObject]
