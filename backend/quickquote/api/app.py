"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import FRONTEND_DIR
from ..reference.loader import get_reference_data
from .routes import router

DESCRIPTION = """
Converts customer quote sheets and finished product specifications into a
review-ready quote package.

All cost math, matching and flag generation is deterministic. Document
parsing may be assisted, but no assisted output is ever used for cost values,
match decisions, confidence levels or flags.

The system prepares quote packages. It does not finalise pricing, replace
human review, or act as a system of record.
""".strip()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_reference_data()  # fail fast if the reference tables cannot be read
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Quick Quote System",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)

    index = FRONTEND_DIR / "index.html"
    static = FRONTEND_DIR / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

    if index.is_file():
        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(index)

    return app


app = create_app()
