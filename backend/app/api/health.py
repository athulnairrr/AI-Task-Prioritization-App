from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Basic liveness check used by Docker/CI and uptime monitors."""
    return {"status": "ok"}
