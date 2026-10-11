from fastapi import APIRouter, status

from app.api.deps import ChatServiceDep, CurrentUser
from app.schemas.chat import ChatExchangeOut, ChatHistoryOut, ChatMessageCreate, ChatMessageOut, ChatMeta

router = APIRouter(prefix="/observations/{observation_id}/chat", tags=["chat"])

_ERRORS = {
    404: {"description": "Observation not found (or not yours)."},
    409: {"description": "The observation has no finished analysis yet (`analysis_required` / `analysis_in_progress`)."},
}


@router.post(
    "",
    response_model=ChatExchangeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the AI a question about this observation",
    responses={
        **_ERRORS,
        422: {"description": "Invalid message (blank, no letters/numbers, or over 1000 characters)."},
        429: {"description": "Rate limit exceeded; see the `Retry-After` header."},
        502: {"description": "The AI service failed or returned an unusable answer."},
        503: {"description": "AI is not configured on this server."},
        504: {"description": "The AI service timed out."},
    },
)
def send_message(
    observation_id: int, data: ChatMessageCreate, user: CurrentUser, service: ChatServiceDep
) -> ChatExchangeOut:
    """Gemini answers using this observation's photo analysis, lesson, challenge, action, weather, place
    and the previous messages in the thread. The question and answer are stored together, only on success."""
    thread_id, user_message, assistant_message, outcome = service.send_message(user, observation_id, data.message)
    return ChatExchangeOut(
        thread_id=thread_id,
        user_message=ChatMessageOut.model_validate(user_message),
        assistant_message=ChatMessageOut.model_validate(assistant_message),
        meta=ChatMeta(
            model=outcome.model,
            prompt_version=outcome.prompt_version,
            attempts=outcome.attempts,
            tokens_used=outcome.total_tokens,
            latency_ms=outcome.latency_ms,
        ),
    )


@router.get("", response_model=ChatHistoryOut, summary="Chat history for this observation", responses={404: _ERRORS[404]})
def get_history(observation_id: int, user: CurrentUser, service: ChatServiceDep) -> ChatHistoryOut:
    thread_id, messages = service.history(user, observation_id)
    return ChatHistoryOut(thread_id=thread_id, messages=[ChatMessageOut.model_validate(m) for m in messages])


@router.get(
    "/suggestions",
    response_model=list[str],
    summary="Suggested questions for this observation",
    responses=_ERRORS,
)
def get_suggestions(observation_id: int, user: CurrentUser, service: ChatServiceDep) -> list[str]:
    """Up to 4 starter questions built from the observation (no AI call). Questions already asked are skipped."""
    return service.suggestions(user, observation_id)
