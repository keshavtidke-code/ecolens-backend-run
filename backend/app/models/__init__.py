"""Import every model so Base.metadata is complete (Alembic autogenerate relies on this)."""
from app.models.action import RecommendedAction
from app.models.analysis import Analysis
from app.models.challenge import Challenge
from app.models.chat import ChatMessage, ChatThread
from app.models.journal import JournalEntry
from app.models.lesson import Lesson
from app.models.observation import Observation
from app.models.user import User
from app.models.weather import WeatherSnapshot

__all__ = [
    "Analysis",
    "Challenge",
    "ChatMessage",
    "ChatThread",
    "JournalEntry",
    "Lesson",
    "Observation",
    "RecommendedAction",
    "User",
    "WeatherSnapshot",
]
