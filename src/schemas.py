"""Shared Pydantic schemas and validation helpers.

Keep these minimal and reusable to enforce consistent input validation
across all API routes.
"""

from pydantic import BaseModel


class StrictBaseModel(BaseModel):
    """Base model that rejects unknown fields and trims strings."""

    class Config:
        extra = "forbid"
        anystr_strip_whitespace = True
        validate_assignment = True
        min_anystr_length = 1
