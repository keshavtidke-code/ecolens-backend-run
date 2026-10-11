from enum import Enum


class ObservationStatus(str, Enum):
    pending = "pending"
    analyzing = "analyzing"
    complete = "complete"
    failed = "failed"


class PhotoSource(str, Enum):
    camera = "camera"
    gallery = "gallery"


class ChallengeStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    completed = "completed"
    skipped = "skipped"


class ChatRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ActionStatus(str, Enum):
    open = "open"
    completed = "completed"
    skipped = "skipped"
