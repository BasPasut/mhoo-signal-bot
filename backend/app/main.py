import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import settings
from app.models.db import create_db_and_tables
from app.scheduler.runner import start_scheduler
from app.scheduler.outcome_tracker import start_outcome_tracker
from app.discord.bot import start_bot
from app.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    create_db_and_tables()
    start_scheduler()
    start_outcome_tracker()
    asyncio.create_task(start_bot())
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Mhoo Signal Bot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {"message": "Mhoo Signal Bot API", "docs": "/docs"}
