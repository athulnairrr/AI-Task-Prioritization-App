from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.calendar import router as calendar_router
from app.api.health import router as health_router
from app.api.me import router as me_router
from app.api.scheduling import router as scheduling_router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.db import close_pool

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_pool()


app = FastAPI(
    title="AI Work Planner API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(me_router)
# scheduling_router (POST /tasks/schedule) must be registered before
# tasks_router (GET/PATCH/DELETE /tasks/{task_id}) so the literal
# "/tasks/schedule" path is matched before the {task_id} pattern.
app.include_router(scheduling_router)
app.include_router(tasks_router)
app.include_router(calendar_router)
