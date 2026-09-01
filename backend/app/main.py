"""FastAPI application entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import constructs
from app.db.session import init_db

DESCRIPTION = """
Circular DNA (plasmid) viewer and editor.

Current state is never stored: it is derived by replaying the non-reverted
operations of a construct's append-only log over its base sequence and base
features. That makes undo/redo a flag flip and keeps the whole edit history
auditable.
"""

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="visorADN",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(constructs.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
