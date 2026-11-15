from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import Settings

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    version: str
    env: str


class Readiness(BaseModel):
    status: str
    dependencies: dict[str, str]


@router.get("/health", response_model=Health)
def health(request: Request) -> Health:
    s: Settings = request.app.state.settings
    return Health(status="ok", version=s.version, env=s.env)


@router.get("/health/ready", response_model=Readiness)
def ready(request: Request) -> Readiness:
    s: Settings = request.app.state.settings
    # Real connectivity checks are added with the persistence slice.
    deps = {
        "database": "configured" if s.database_url else "not_configured",
        "redis": "configured" if s.redis_url else "not_configured",
    }
    return Readiness(status="ok", dependencies=deps)
