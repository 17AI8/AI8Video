"""HTTP-shaped response builders for the main Agent."""

from __future__ import annotations

from typing import Any

from ai8video.application.agent_journal import AgentJournal


def agent_pending_response(
    conversation: dict[str, Any],
    run: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reply": {
            "text": "Agent 已把高层动作交给 Runtime。Runtime 会自行轮询、下载和归档，只有出现终态结果时才重新唤醒 Agent。",
            "stage": "pending",
            "awaiting": None,
            "draft": None,
            "meta": {"operation": "agent_runtime", "agentRunId": run["id"]},
            "result": None,
        },
        "status": "pending",
        "phase": "runtime",
        "sessionId": conversation["id"],
        "generationBatchId": payload.get("generationBatchId"),
        "agentRun": run,
        "agentAction": payload,
        "conversation": conversation,
        "chatBackend": "ai8video-main-agent",
    }


def agent_waiting_user_response(
    conversation: dict[str, Any],
    run: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    pending = context.get("pendingApproval") or context.get("pendingUserQuestion") or {}
    run_error = run.get("error") if isinstance(run.get("error"), dict) else {}
    question = str(pending.get("question") or run_error.get("message") or "需要你确认下一步。")
    return {
        "reply": {
            "text": question,
            "stage": "collecting",
            "awaiting": "agent_user_confirmation",
            "draft": None,
            "meta": {"operation": "agent_waiting_user", "agentRunId": run["id"]},
            "result": None,
        },
        "status": "waiting_user",
        "agentRun": run,
        "approval": context.get("pendingApproval"),
        "conversation": conversation,
        "chatBackend": "ai8video-main-agent",
    }


def agent_terminal_response(
    conversation: dict[str, Any],
    run: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    final_delivery = context.get("finalDelivery") or {}
    text = str(context.get("finalText") or final_delivery.get("summary") or "Agent 本轮已结束。")
    stage = "completed" if run["state"] == "succeeded" else "error"
    return {
        "reply": {
            "text": text,
            "stage": stage,
            "awaiting": None,
            "draft": None,
            "meta": {"operation": "agent_complete", "agentRunId": run["id"]},
            "result": final_delivery or None,
        },
        "status": run["state"],
        "agentRun": run,
        "conversation": conversation,
        "chatBackend": "ai8video-main-agent",
    }


def agent_error_response(
    journal: AgentJournal,
    conversation: dict[str, Any] | None,
    run_id: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "reply": {
            "text": message,
            "stage": "error",
            "awaiting": None,
            "draft": None,
            "meta": {"operation": "agent_error", "agentRunId": run_id},
            "result": None,
        },
        "status": "failed",
        "error": {"code": code, "message": message},
        "agentRun": journal.get_run(run_id),
        "conversation": conversation,
        "chatBackend": "ai8video-main-agent",
    }
