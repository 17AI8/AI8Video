"""Conversation lifecycle and Agent journal HTTP routes."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

from bottle import HTTPResponse, request, response

from ai8video.application.agent_journal import AgentJournal
from ai8video.application.agent_controller import get_main_agent
from ai8video.application.conversation_store import ConversationStore, ConversationStoreError
from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.model_profiles import ensure_model_profiles, model_profile_binding_snapshot
from ai8video.integrations.video_model_settings import load_video_model_settings


_STORE: ConversationStore | None = None
_JOURNAL: AgentJournal | None = None
_STORE_PATH: Path | None = None
_STORE_LOCK = Lock()


def get_conversation_store(*, refresh: bool = False) -> ConversationStore:
    global _STORE, _STORE_PATH
    configured = os.getenv("AI8VIDEO_CONVERSATION_STORE_PATH")
    candidate = Path(configured).expanduser().resolve() if configured else None
    with _STORE_LOCK:
        if refresh or _STORE is None or (candidate is not None and candidate != _STORE_PATH):
            _STORE = ConversationStore(candidate)
            _STORE_PATH = _STORE.path
        return _STORE


def get_agent_journal(*, refresh: bool = False) -> AgentJournal:
    global _JOURNAL
    store = get_conversation_store(refresh=refresh)
    with _STORE_LOCK:
        if refresh or _JOURNAL is None or _JOURNAL.path != store.path:
            _JOURNAL = AgentJournal(store.path)
        return _JOURNAL


def reset_conversation_route_state() -> None:
    global _STORE, _JOURNAL, _STORE_PATH
    with _STORE_LOCK:
        _STORE = None
        _JOURNAL = None
        _STORE_PATH = None


def ensure_conversation_for_chat(
    conversation_id: str,
    *,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    store = get_conversation_store()
    try:
        return store.get_conversation(conversation_id)
    except ConversationStoreError as exc:
        if exc.code != "conversation_not_found":
            raise
    try:
        return store.create_conversation(
            conversation_id=conversation_id,
            execution_mode=execution_mode or "workflow",
            legacy_adopted=True,
            allow_over_limit=True,
        )
    except ConversationStoreError as exc:
        if exc.code != "conversation_exists":
            raise
        return store.get_conversation(conversation_id)


def lock_conversation_for_chat(
    conversation_id: str,
    message: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    expected_mode = str(
        payload.get("expectedExecutionMode") or payload.get("executionMode") or ""
    ).strip() or None
    current = ensure_conversation_for_chat(conversation_id, execution_mode="workflow")
    mode = str(current.get("executionMode") or "workflow")
    if expected_mode and expected_mode != mode:
        raise ConversationStoreError(
            "conversation_mode_conflict",
            "页面中的执行模式已经过期，请刷新对话状态后重试。",
            details={"expected": expected_mode, "actual": mode},
        )
    revision_value = payload.get("expectedRevision", payload.get("conversationRevision"))
    expected_revision = int(revision_value) if revision_value is not None else None
    return get_conversation_store().lock_for_message(
        conversation_id,
        message,
        execution_mode=mode,
        expected_revision=expected_revision,
        client_message_id=str(payload.get("clientMessageId") or "").strip() or None,
        model_binding_factory=_current_model_binding_snapshot,
        create_agent_run=_agent_mode_enabled(),
    )


def append_assistant_message(
    conversation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    reply = body.get("reply") if isinstance(body.get("reply"), dict) else {}
    text = str(reply.get("text") or "").strip()
    if text:
        get_conversation_store().append_message(
            conversation_id,
            role="assistant",
            content=text,
            metadata={
                "stage": reply.get("stage"),
                "operation": (reply.get("meta") or {}).get("operation")
                if isinstance(reply.get("meta"), dict)
                else None,
                "legacyPayload": reply,
            },
        )
    body["conversation"] = get_conversation_store().get_conversation(conversation_id)
    return body


def api_conversations():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    store = get_conversation_store()
    try:
        if request.method == "POST":
            payload = _json_payload()
            if payload.get("executionMode") == "agent" and not _agent_mode_enabled():
                raise ConversationStoreError(
                    "agent_mode_disabled",
                    "Agent 模式当前已关闭，标准模式仍可继续使用。",
                    status=403,
                )
            conversation = store.create_conversation(
                title=payload.get("title") or "新对话",
                execution_mode=payload.get("executionMode") or "workflow",
            )
            conversations = store.list_conversations()
            response.status = 201
            return {
                "ok": True,
                "conversation": conversation,
                "item": conversation,
                "conversations": conversations,
                "items": conversations,
                **_capacity_payload(store),
            }
        conversations = store.list_conversations()
        return {
            "ok": True,
            "conversations": conversations,
            "items": conversations,
            **_capacity_payload(store),
        }
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_reconcile_conversations():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        payload = _json_payload()
        items = payload.get("conversations")
        if not isinstance(items, list):
            raise ConversationStoreError(
                "invalid_legacy_conversations",
                "conversations 必须是数组。",
                status=400,
            )
        store = get_conversation_store()
        conversations = store.reconcile_legacy(items)
        return {
            "ok": True,
            "conversations": conversations,
            "items": conversations,
            **_capacity_payload(store),
        }
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_conversation_messages(conversation_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        return {
            "ok": True,
            "conversation": get_conversation_store().get_conversation(conversation_id),
            "messages": get_conversation_store().list_messages(conversation_id),
        }
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_conversation_mode(conversation_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        payload = _json_payload()
        if payload.get("executionMode") == "agent" and not _agent_mode_enabled():
            raise ConversationStoreError(
                "agent_mode_disabled",
                "Agent 模式当前已关闭，标准模式仍可继续使用。",
                status=403,
            )
        conversation = get_conversation_store().set_execution_mode(
            conversation_id,
            payload.get("executionMode"),
            expected_revision=_optional_int(payload.get("expectedRevision", payload.get("revision"))),
        )
        return {"ok": True, "conversation": conversation}
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_reset_conversation(conversation_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        conversation = get_conversation_store().reset_conversation(conversation_id)
        from ai8video.application.facade import reset_chat_session
        from ai8video.generation.generation_progress import clear_generation_progress

        reset_chat_session(conversation_id)
        clear_generation_progress(conversation_id)
        return {"ok": True, "conversation": conversation}
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_delete_conversation(conversation_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        store = get_conversation_store()
        result = store.delete_conversation(conversation_id)
        from ai8video.application.facade import reset_chat_session
        from ai8video.generation.generation_progress import clear_generation_progress

        reset_chat_session(conversation_id)
        clear_generation_progress(conversation_id)
        conversations = store.list_conversations()
        return {
            "ok": True,
            **result,
            "conversations": conversations,
            "items": conversations,
            **_capacity_payload(store),
        }
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_agent_run(run_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        journal = get_agent_journal()
        status = get_main_agent(get_conversation_store(), journal).run_status(run_id)
        return {
            "ok": True,
            **status,
            "observations": journal.list_observations(run_id),
        }
    except ConversationStoreError as exc:
        return _store_error(exc)


def api_agent_action_approval(action_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        payload = _json_payload()
        result = get_main_agent(
            get_conversation_store(),
            get_agent_journal(),
        ).handle_approval(action_id, approved=bool(payload.get("approved")))
        conversation = result.get("conversation") if isinstance(result.get("conversation"), dict) else {}
        conversation_id = str(conversation.get("id") or "").strip()
        if conversation_id:
            result = append_assistant_message(conversation_id, result)
        return {"ok": True, "result": result}
    except ConversationStoreError as exc:
        return _store_error(exc)


def register_conversation_routes(app) -> None:
    app.route("/api/conversations", method=["GET", "POST", "OPTIONS"])(api_conversations)
    app.route("/api/conversations/reconcile", method=["POST", "OPTIONS"])(api_reconcile_conversations)
    app.route(
        "/api/conversations/<conversation_id>/messages",
        method=["GET", "OPTIONS"],
    )(api_conversation_messages)
    app.route(
        "/api/conversations/<conversation_id>/execution-mode",
        method=["PATCH", "OPTIONS"],
    )(api_conversation_mode)
    app.route(
        "/api/conversations/<conversation_id>/reset",
        method=["POST", "OPTIONS"],
    )(api_reset_conversation)
    app.route(
        "/api/conversations/<conversation_id>",
        method=["DELETE", "OPTIONS"],
    )(api_delete_conversation)
    app.route("/api/agent-runs/<run_id>", method=["GET", "OPTIONS"])(api_agent_run)
    app.route(
        "/api/agent-actions/<action_id>/approval",
        method=["POST", "OPTIONS"],
    )(api_agent_action_approval)


def _capacity_payload(store: ConversationStore) -> dict[str, Any]:
    count = len(store.list_conversations())
    return {
        "maxConversations": store.max_conversations,
        "conversationLimit": store.max_conversations,
        "conversationCount": count,
        "overLimit": count > store.max_conversations,
        "canCreateConversation": count < store.max_conversations,
        "agentModeEnabled": _agent_mode_enabled(),
    }


def _agent_mode_enabled() -> bool:
    return str(os.getenv("AI8VIDEO_AGENT_MODE_ENABLED") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def agent_mode_enabled() -> bool:
    return _agent_mode_enabled()


def bound_conversations_for_model_profile(category: str, profile_id: str) -> list[dict[str, Any]]:
    identifier = str(profile_id or "").strip()
    if not identifier:
        return []
    conflicts: list[dict[str, Any]] = []
    for conversation in get_conversation_store().list_conversations():
        if not conversation.get("modeLocked"):
            continue
        binding = conversation.get("modelBinding") if isinstance(conversation.get("modelBinding"), dict) else {}
        categories = binding.get("categories") if isinstance(binding.get("categories"), dict) else {}
        selected = categories.get(category) if isinstance(categories.get(category), dict) else {}
        if selected.get("profileId") == identifier:
            conflicts.append({
                "id": conversation["id"],
                "title": conversation["title"],
                "executionMode": conversation["executionMode"],
            })
    return conflicts


def _current_model_binding_snapshot() -> dict[str, Any]:
    config = AI8VideoConfig.from_env()
    video = load_video_model_settings(
        llm_base_url=config.llm_base_url,
        llm_api_key=config.llm_api_key,
    )
    ensure_model_profiles({
        "llm": {
            "baseUrl": config.llm_base_url,
            "apiKey": config.llm_api_key,
            "model": config.llm_model,
        },
        "multimodal": {
            "baseUrl": config.multimodal_base_url,
            "apiKey": config.multimodal_api_key,
            "model": config.multimodal_model,
        },
        "image": {
            "baseUrl": config.image_base_url,
            "apiKey": config.image_api_key,
            "model": config.image_model,
        },
        "video": {
            "baseUrl": video.base_url,
            "apiKey": video.api_key,
            "model": video.model,
            "template": video.template,
        },
    })
    return model_profile_binding_snapshot()


def _json_payload() -> dict[str, Any]:
    payload = request.json or {}
    if not isinstance(payload, dict):
        raise ConversationStoreError("invalid_payload", "payload 必须是对象。", status=400)
    return payload


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConversationStoreError("invalid_revision", "revision 必须是整数。", status=400) from exc


def _store_error(error: ConversationStoreError) -> dict[str, Any]:
    response.status = error.status
    return {
        "ok": False,
        "code": error.code.upper(),
        "error": str(error),
        "details": error.details,
    }
