"""HTTP rendering for stable OpenAI-style application errors."""

from fastapi import Request
from fastapi.responses import JSONResponse

from prism.domain.errors import PrismError


async def prism_error_handler(_: Request, error: PrismError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return JSONResponse(
        status_code=error.status_code,
        headers=headers,
        content={
            "error": {
                "message": error.message,
                "type": error.error_type,
                "param": None,
                "code": error.code,
            }
        },
    )
