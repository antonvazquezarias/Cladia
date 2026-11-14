"""Cladia HTTP application assembly; domain routes live in api_* modules."""
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from . import library
from .request_context import _window
from .item_inputs import InputError
from . import api_exports, api_graphs, api_library, api_locks, api_mappings, api_metadata, api_results, api_sequences, api_settings, api_tools
from .project_api import router as project_router

app = FastAPI(title="Cladia")

@app.exception_handler(library.DestinationError)
async def destination_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.exception_handler(InputError)
async def input_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.middleware("http")
async def remember_window(request: Request, call_next):
    _window.set(request.headers.get("x-cladia-window"))
    return await call_next(request)

app.include_router(api_locks.router)
app.include_router(api_exports.router)
app.include_router(api_graphs.router)
app.include_router(api_library.router)
app.include_router(api_mappings.router)
app.include_router(api_metadata.router)
app.include_router(api_results.router)
app.include_router(api_sequences.router)
app.include_router(api_settings.router)
app.include_router(api_tools.router)
app.include_router(project_router)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
