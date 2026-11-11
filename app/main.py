from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import assets, pipeline, predictions, reports
from app.bootstrap import Container, build_container
from app.config import Settings
from app.logging_config import configure_logging


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container is None:
            settings = Settings()
            configure_logging(settings.log_level, settings.secrets)
            app.state.container = build_container(settings)     # model artifact is loaded ONCE here
        else:
            app.state.container = container
        yield
        await app.state.container.close()

    app = FastAPI(title="Heng Seng Sentiment & Price Prediction Agent", version="0.1.0", lifespan=lifespan,
                  description="Predictions are model estimates, not financial advice.")
    origins = (container.settings.cors_origins if container else Settings().cors_origins).split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins], allow_methods=["*"], allow_headers=["*"])
    for r in (pipeline.router, assets.router, predictions.router, reports.router):
        app.include_router(r)

    @app.get("/health", tags=["health"])
    async def health():
        c = app.state.container
        return {"status": "ok", "model_loaded": c.model_service.available,
                "model_unavailable_reason": c.model_service.unavailable_reason, "storage": c.settings.storage_backend}

    return app


app = create_app()
