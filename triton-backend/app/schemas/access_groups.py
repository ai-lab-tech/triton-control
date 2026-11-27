from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, PositiveInt, StringConstraints


class AccessGroupRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    user_ids: set[PositiveInt] = Field(default_factory=set)
    instance_ids: set[PositiveInt] = Field(default_factory=set)


class AccessGroupUpdate(AccessGroupRequest):
    revision: PositiveInt
    user_ids: set[PositiveInt] = Field()
    instance_ids: set[PositiveInt] = Field()


class AccessGroupDTO(AccessGroupUpdate):
    id: UUID


class AccessGroupInstanceOption(BaseModel):
    id: int
    name: str
