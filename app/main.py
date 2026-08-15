from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    evaluations_router,
    explanations_router,
    health_router,
    knowledge_router,
    platform_router,
    screenings_router,
    sources_router,
    workflow_router,
)
from app.core.exceptions import BaoxiaoError
from app.core.logging import configure_logging, logger

configure_logging()

app = FastAPI(
    title="保销智审 API",
    version="0.1.0",
    description="保险营销合规审查的可信数据采集、解析、校验、人工审核与索引状态 API。",
)
app.include_router(health_router)
app.include_router(explanations_router)
app.include_router(evaluations_router)
app.include_router(sources_router)
app.include_router(workflow_router)
app.include_router(knowledge_router)
app.include_router(screenings_router)
app.include_router(platform_router)

WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
async def demo_workspace() -> FileResponse:
    """Serve the zero-dependency contest demonstration workspace."""
    return FileResponse(WEB_DIR / "index.html")


@app.exception_handler(BaoxiaoError)
async def domain_exception(request: Request, exc: BaoxiaoError) -> JSONResponse:
    code = str(exc) or exc.__class__.__name__
    logger.warning("domain_exception", path=request.url.path, code=code)
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": code,
                "message": code,
                "details": None,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "details": None,
            }
        },
    )
