from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Body, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AnalysisServiceDep, CaptureServiceDep, CurrentUser, LearningServiceDep, StorageDep
from app.core.exceptions import NotFoundError
from app.models.enums import ObservationStatus, PhotoSource
from app.schemas.common import Page
from app.schemas.learning import (
    ActionContentOut,
    ChallengeContentOut,
    LearningExperienceOut,
    LearningExperienceRequest,
    LearningMeta,
    LessonContentOut,
)
from app.schemas.observation import AnalysisOut, ObservationDetail, ObservationOut, ObservationStatusOut

router = APIRouter(prefix="/observations", tags=["observations"])


@router.post("", response_model=ObservationDetail, status_code=status.HTTP_201_CREATED)
def create_observation(
    user: CurrentUser,
    service: CaptureServiceDep,
    photo: Annotated[UploadFile, File(description="JPEG, PNG, WebP or HEIC photo")],
    latitude: Annotated[float, Form(ge=-90, le=90)],
    longitude: Annotated[float, Form(ge=-180, le=180)],
    captured_at: Annotated[Optional[datetime], Form()] = None,
    location_accuracy_m: Annotated[Optional[float], Form(ge=0)] = None,
    photo_source: Annotated[PhotoSource, Form()] = PhotoSource.camera,
    user_note: Annotated[Optional[str], Form(max_length=2000)] = None,
) -> ObservationDetail:
    observation = service.create(
        user,
        upload=photo,
        latitude=latitude,
        longitude=longitude,
        captured_at=captured_at,
        location_accuracy_m=location_accuracy_m,
        photo_source=photo_source,
        user_note=user_note,
    )
    return ObservationDetail.model_validate(observation)


@router.get("", response_model=Page[ObservationOut])
def list_observations(
    user: CurrentUser,
    service: CaptureServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[Optional[int], Query(description="next_cursor from the previous page")] = None,
    status_filter: Annotated[Optional[ObservationStatus], Query(alias="status")] = None,
    captured_from: Optional[datetime] = None,
    captured_to: Optional[datetime] = None,
) -> Page[ObservationOut]:
    items, next_cursor = service.observations.list_for_user(
        user.id,
        limit=limit,
        cursor=cursor,
        status=status_filter.value if status_filter else None,
        captured_from=captured_from,
        captured_to=captured_to,
    )
    return Page[ObservationOut](
        items=[ObservationOut.model_validate(o) for o in items], next_cursor=next_cursor
    )


@router.get("/{observation_id}", response_model=ObservationDetail)
def get_observation(
    observation_id: int, user: CurrentUser, service: CaptureServiceDep
) -> ObservationDetail:
    return ObservationDetail.model_validate(service.get_owned(user, observation_id, detail=True))


@router.get("/{observation_id}/status", response_model=ObservationStatusOut)
def get_observation_status(
    observation_id: int, user: CurrentUser, service: CaptureServiceDep
) -> ObservationStatusOut:
    return ObservationStatusOut.model_validate(service.get_owned(user, observation_id))


@router.get("/{observation_id}/photo", response_class=FileResponse)
def get_observation_photo(
    observation_id: int, user: CurrentUser, service: CaptureServiceDep, storage: StorageDep
) -> FileResponse:
    observation = service.get_owned(user, observation_id)
    path = storage.path_for(observation.photo_path) if observation.photo_path else None
    if path is None or not path.is_file():
        raise NotFoundError("Photo not found.", code="photo_not_found")
    # Guard against corrupt stub files that slipped through (e.g., from pre-fix uploads).
    if path.stat().st_size < 512:
        raise NotFoundError(
            "The stored photo is corrupt or incomplete. Please retake the photo.",
            code="photo_corrupt",
        )
    return FileResponse(
        path,
        media_type=observation.photo_mime_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post(
    "/{observation_id}/analyze",
    response_model=AnalysisOut,
    responses={
        400: {"description": "The AI declined to analyze this image (content policy)"},
        404: {"description": "Observation or its photo not found"},
        409: {"description": "An analysis is already running for this observation"},
        502: {"description": "AI provider failed or returned invalid output"},
        503: {"description": "GEMINI_API_KEY is not configured"},
        504: {"description": "AI provider timed out"},
    },
)
def analyze_observation(
    observation_id: int, user: CurrentUser, analysis: AnalysisServiceDep
) -> AnalysisOut:
    """Run the Gemini vision perception stage (photo + location + weather) and store the result.

    Synchronous: expect roughly 5-30 seconds. Calling it again creates a newer analysis.
    """
    return AnalysisOut.model_validate(analysis.analyze(user, observation_id))


@router.post(
    "/{observation_id}/learning-experience",
    response_model=LearningExperienceOut,
    responses={
        404: {"description": "Observation not found"},
        409: {"description": "Photo analysis missing, still running, or unusable"},
        502: {"description": "AI provider failed or returned an invalid lesson"},
        503: {"description": "GEMINI_API_KEY is not configured"},
        504: {"description": "AI provider timed out"},
    },
)
def create_learning_experience(
    observation_id: int,
    user: CurrentUser,
    learning: LearningServiceDep,
    body: Annotated[Optional[LearningExperienceRequest], Body()] = None,
    regenerate: Annotated[
        bool, Query(description="Generate a fresh lesson even if one already exists for this analysis")
    ] = False,
) -> LearningExperienceOut:
    """Stage 2: turn the stored photo analysis + place + weather into a personalized lesson,
    one observation challenge and one recommended action.

    Requires a completed analysis (`POST .../analyze`). The body is optional. Calling again with the
    same knowledge level returns the existing result (no AI cost) unless `regenerate=true`.
    """
    request = body or LearningExperienceRequest()
    result = learning.generate(
        user,
        observation_id,
        knowledge_level=request.knowledge_level,
        interests=request.interests,
        regenerate=regenerate,
    )
    return LearningExperienceOut(
        lesson=LessonContentOut.from_lesson(result.lesson),
        challenge=ChallengeContentOut.from_challenge(result.challenge),
        recommended_action=ActionContentOut.from_action(result.action),
        meta=LearningMeta(
            analysis_id=result.analysis.id,
            prompt_version=result.lesson.prompt_version,
            model=result.lesson.model_name,
            cautious_mode=result.cautious,
            weather_available=result.weather_available,
            reused=result.reused,
        ),
    )


@router.post(
    "/{observation_id}/refresh-context",
    response_model=ObservationDetail,
    responses={
        502: {"description": "Weather/geocoding provider failed"},
        504: {"description": "Weather/geocoding provider timed out"},
    },
)
def refresh_observation_context(
    observation_id: int, user: CurrentUser, service: CaptureServiceDep
) -> ObservationDetail:
    """Re-fetch place name and weather for this observation's coordinates and timestamp."""
    return ObservationDetail.model_validate(service.refresh_context(user, observation_id))


@router.delete("/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(
    observation_id: int, user: CurrentUser, service: CaptureServiceDep
) -> Response:
    service.delete(user, observation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
