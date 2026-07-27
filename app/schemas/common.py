from typing import Any

from pydantic import BaseModel


class MessageResponse(BaseModel):
    message: str
    data: Any = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
