from fastapi import APIRouter

from app.services.model_manager import get_llama_server_manager

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    return {"status": "ready"}


@router.get("/status")
def status() -> dict:
    return {"status": "ok", "managed_llama": get_llama_server_manager().status()}
