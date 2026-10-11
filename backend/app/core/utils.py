from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def coarse_coords(latitude: float, longitude: float) -> str:
    """Rounded (~1 km) coordinates for logs; never log exact user locations."""
    return f"{latitude:.2f},{longitude:.2f}"
