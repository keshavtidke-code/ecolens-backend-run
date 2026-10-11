from fastapi import APIRouter

from app.api.v1 import auth, chat, journal, observations, weather

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(observations.router)
api_router.include_router(chat.router)
api_router.include_router(weather.router)
api_router.include_router(journal.router)
