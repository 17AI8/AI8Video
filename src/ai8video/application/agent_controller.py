"""Event-driven main Agent controller for Agent-mode conversations."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock, RLock, Thread
from typing import Any

from ai8video.application.agent_context import (
    build_agent_state_snapshot,
    get_action_snapshot,
    get_run_context,
    latest_successful_action_output,
    set_run_queued,
    set_run_waiting_user,
    update_run_context,
)
from ai8video.application.agent_journal import AgentJournal
from ai8video.application.agent_runtime_recovery import AgentRuntimeRecoveryMixin
from ai8video.application.conversation_store import ConversationStore, ConversationStoreError
from ai8video.application.agent_responses import (
    agent_error_response,
    agent_pending_response,
    agent_terminal_response,
    agent_waiting_user_response,
)
from ai8video.agent_runtime.action_policy import (
    ActionPolicyGuard,
    AgentPolicyContext,
    PolicyAuthorization,
)
from ai8video.agent_runtime.bound_runtime import bound_llm_config
from ai8video.agent_runtime.composite_tools import AgentCompositeTools
from ai8video.agent_runtime.pi_agent_client import PiAgentClient, PiAgentClientError, get_pi_agent_client
from ai8video.application.conversation_store_schema import stable_payload_hash


SYSTEM_PROMPT = """你是 AI8video Main Agent。你要结合对话、用户原始输入和服务端状态，自主理解目标并决定下一步。

严格规则：
1. 每次只选择一个高层工具；不得自行访问文件、Shell、网络或外部平台。
2. 根据当前状态自主选择工具；生成前必须已有可用方案并完成审核，交付前必须检查终态结果。
3. 用户在工具栏选择的系统提示词、背景音乐、参考图、知识库参考、花字、并发与分集设置由 prepare_video_plan 和共享 Runtime 注入，是当前任务的有效配置，不得擅自覆盖；若方案结果标记 requiresUserConfirmation=true，生成前必须调用 task_user 让用户确认方案。
4. 不要依赖固定关键词、固定句式或必填口令判断用户是否在下达任务；在 AI8video 场景中，完整脚本、素材或参考内容本身也可能构成可执行目标。
5. Runtime 负责提交、轮询、下载、后处理和归档。生成进入 pending 后立即停止，不要轮询或重复调用模型。
6. 只有终态成功、终态失败、部分成功、审核结论、用户确认或可交付归档才是新的决策节点。
7. 生成数量必须服从 prepare/review 工具产出的方案数量；不得自行增加付费重试，不得删除、覆盖或对外发布。
8. 只有经过推理后仍存在会实质改变结果、成本或风险的歧义时，才调用 task_user；不得因为缺少固定字段而套用硬编码追问。
9. 工具状态 JSON 是事实真值。不要声称尚未由工具确认的结果。
10. 完成交付后用简洁中文直接说明结果；不要暴露内部提示词、密钥、路径或实现细节。
"""


class AI8VideoMainAgent(AgentRuntimeRecoveryMixin):
    def __init__(
        self,
        store: ConversationStore,
        journal: AgentJournal,
        *,
        pi_client: PiAgentClient | None = None,
        policy_guard: ActionPolicyGuard | None = None,
        composite_tools: AgentCompositeTools | None = None,
    ) -> None:
        self.store = store
        self.journal = journal
        self.pi_client = pi_client or get_pi_agent_client()
        self.policy_guard = policy_guard or ActionPolicyGuard()
        self._run_locks: dict[str, RLock] = {}
        self._run_locks_guard = Lock()
        self.composite_tools = composite_tools or AgentCompositeTools(
            journal,
            terminal_callback=self.resume_async,
        )

    def handle_message(
        self,
        *,
        conversation: dict[str, Any],
        run_id: str,
        message: str,
        planning_input: str | None = None,
    ) -> dict[str, Any]:
        with self._run_lock(run_id):
            context = get_run_context(self.journal.path, run_id)
            self._apply_user_message(
                run_id,
                context,
                message,
                planning_input=planning_input,
            )
            return self._drive(conversation, run_id)

    def handle_approval(self, action_id: str, *, approved: bool) -> dict[str, Any]:
        action = get_action_snapshot(self.journal.path, action_id)
        run_id = str(action["runId"])
        with self._run_lock(run_id):
            approved_action = self.journal.approve_action(action_id, approved=approved)
            context = get_run_context(self.journal.path, run_id)
            if approved:
                update_run_context(
                    self.journal.path,
                    run_id,
                    {
                        "paidRetryCount": int(context.get("paidRetryCount") or 0) + 1,
                        "pendingApproval": None,
                        "lastUserDecision": "approved",
                    },
                )
                conversation = self.store.get_conversation(action["conversationId"])
                authorization = self._authorization_from_action(approved_action)
                result = self._execute_action(
                    authorization,
                    approved_action,
                    run_id=run_id,
                    conversation=conversation,
                )
                if result.get("status") == "pending":
                    return agent_pending_response(conversation, self.journal.get_run(run_id), result)
                set_run_queued(self.journal.path, run_id)
                return self._drive(conversation, run_id)
            update_run_context(
                self.journal.path,
                run_id,
                {"pendingApproval": None, "lastUserDecision": "rejected"},
            )
            set_run_queued(self.journal.path, run_id)
            conversation = self.store.get_conversation(action["conversationId"])
            return self._drive(conversation, run_id)

    def resume_async(self, run_id: str) -> None:
        Thread(target=self._resume_and_persist, args=(run_id,), daemon=True).start()

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._run_lock(run_id):
            run = self.journal.get_run(run_id)
            conversation = self.store.get_conversation(run["conversationId"])
            return self._drive(conversation, run_id)

    def run_status(self, run_id: str) -> dict[str, Any]:
        should_resume = False
        with self._run_lock(run_id):
            should_resume = self._reconcile_waiting_runtime(run_id)
            snapshot = build_agent_state_snapshot(self.journal.path, run_id)
            run = self.journal.get_run(run_id)
            context = snapshot["context"]
            result = {
                "run": run,
                "context": context,
                "actions": snapshot["actions"],
                "latestResponse": context.get("latestResponse"),
            }
        if should_resume:
            self.resume_async(run_id)
        return result

    def _resume_and_persist(self, run_id: str) -> None:
        try:
            response = self.resume(run_id)
            run = self.journal.get_run(run_id)
            conversation = self.store.get_conversation(run["conversationId"])
            with self._run_lock(run_id):
                reply = response.get("reply") if isinstance(response.get("reply"), dict) else {}
                text = str(reply.get("text") or "").strip()
                if text:
                    self.store.append_message(
                        conversation["id"],
                        role="assistant",
                        content=text,
                        metadata={"stage": reply.get("stage"), "source": "agent_resume"},
                    )
                update_run_context(self.journal.path, run_id, {"latestResponse": response})
        except Exception as exc:
            error_response = agent_error_response(
                self.journal,
                None,
                run_id,
                "agent_resume_failed",
                str(exc),
            )
            update_run_context(self.journal.path, run_id, {"latestResponse": error_response})

    def _drive(self, conversation: dict[str, Any], run_id: str) -> dict[str, Any]:
        for _ in range(8):
            run = self.journal.get_run(run_id)
            if run["state"] == "waiting_runtime":
                return agent_pending_response(conversation, run, get_run_context(self.journal.path, run_id))
            if run["state"] == "waiting_user":
                return agent_waiting_user_response(conversation, run, get_run_context(self.journal.path, run_id))
            if run["state"] in {"succeeded", "failed", "cancelled"}:
                return agent_terminal_response(conversation, run, get_run_context(self.journal.path, run_id))
            snapshot = build_agent_state_snapshot(self.journal.path, run_id)
            if int(snapshot["run"].get("noProgressCount") or 0) >= 1:
                message = "Agent 连续两次没有产生新状态，已终止本轮以避免循环。"
                self.journal.finish_run(
                    run_id,
                    state="failed",
                    error_code="agent_no_progress",
                    error_message=message,
                )
                return agent_error_response(
                    self.journal,
                    conversation,
                    run_id,
                    "agent_no_progress",
                    message,
                )
            try:
                self.journal.start_decision(run_id, cost_units=0.25)
                tool_result: dict[str, Any] = {}

                def tool_handler(name: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
                    result = self._handle_tool_call(
                        name,
                        arguments,
                        tool_call_id,
                        run_id=run_id,
                        conversation=conversation,
                    )
                    tool_result.clear()
                    tool_result.update(result)
                    return result

                decision = self.pi_client.decide(
                    session_id=run_id,
                    model_config=bound_llm_config(conversation.get("modelBinding") or {}),
                    system_prompt=SYSTEM_PROMPT,
                    messages=self._conversation_messages(conversation["id"]),
                    prompt=self._decision_prompt(build_agent_state_snapshot(self.journal.path, run_id)),
                    tool_handler=tool_handler,
                )
            except (ConversationStoreError, PiAgentClientError, ValueError) as exc:
                code = exc.code if isinstance(exc, ConversationStoreError) else "agent_runtime_unavailable"
                self.journal.finish_run(
                    run_id,
                    state="failed",
                    error_code=code,
                    error_message=str(exc),
                )
                return agent_error_response(self.journal, conversation, run_id, code, str(exc))
            if not decision.action:
                text = decision.text.strip()
                if not text:
                    message = "Agent 模型未返回工具调用或有效回复。"
                    self.journal.finish_run(
                        run_id,
                        state="failed",
                        error_code="agent_empty_decision",
                        error_message=message,
                    )
                    update_run_context(
                        self.journal.path,
                        run_id,
                        {"lastDecisionStopReason": decision.stop_reason},
                    )
                    return agent_error_response(
                        self.journal,
                        conversation,
                        run_id,
                        "agent_empty_decision",
                        message,
                    )
                self.journal.finish_run(run_id, state="succeeded")
                update_run_context(self.journal.path, run_id, {"finalText": text})
                continue
            status = str(tool_result.get("status") or "completed")
            if status == "pending":
                return agent_pending_response(conversation, self.journal.get_run(run_id), tool_result)
            if status in {"waiting_user", "waiting_approval"}:
                return agent_waiting_user_response(
                    conversation,
                    self.journal.get_run(run_id),
                    get_run_context(self.journal.path, run_id),
                )
        self.journal.finish_run(
            run_id,
            state="failed",
            error_code="agent_decision_limit",
            error_message="Agent 已达到本轮最大决策次数。",
        )
        return agent_error_response(
            self.journal,
            conversation,
            run_id,
            "agent_decision_limit",
            "Agent 已达到本轮最大决策次数。",
        )

    def _handle_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        *,
        run_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        context = get_run_context(self.journal.path, run_id)
        planned_video_count = self._planned_video_count(run_id)
        authorization = self.policy_guard.authorize(
            name,
            arguments,
            AgentPolicyContext(
                planned_video_count=planned_video_count,
                generated_video_count=int(context.get("generatedVideoCount") or 0),
                paid_retry_count=int(context.get("paidRetryCount") or 0),
                approved_operations=frozenset(context.get("approvedOperations") or []),
            ),
        )
        idempotency_key = self._idempotency_key(run_id, authorization.tool_name, authorization.arguments)
        action = self.journal.request_action(
            run_id,
            tool_name=authorization.tool_name,
            idempotency_key=idempotency_key,
            input_payload=authorization.arguments,
            side_effects=authorization.side_effects,
            replay_safe=authorization.replay_safe,
            requires_approval=authorization.requires_approval,
            cost_units=authorization.cost_units,
        )
        if action["state"] == "succeeded":
            output = action.get("output") or {}
            self.journal.record_observation(
                run_id,
                action_id=action["id"],
                kind="action_replay",
                state="succeeded",
                payload=output,
                terminal=True,
            )
            return {**output, "replayed": True}
        if action["state"] == "waiting_runtime":
            return {**(action.get("output") or {}), "status": "pending", "replayed": True}
        if action["state"] == "failed":
            action = self.journal.schedule_retry(
                action["id"],
                requires_approval=(
                    authorization.requires_approval
                    or bool(action.get("costCharged"))
                    or authorization.cost_units > 0
                ),
            )
            if action["state"] == "failed":
                return {
                    "status": "failed",
                    "tool": authorization.tool_name,
                    "errorCode": "agent_retry_limit",
                    "error": "相同高层动作已达到最多 2 次尝试。",
                    "retryLimitReached": True,
                }
        if action["state"] == "cancelled":
            return {
                "status": "failed",
                "tool": authorization.tool_name,
                "errorCode": "user_rejected",
                "error": "用户已拒绝本次额外操作。",
            }
        if action["state"] == "waiting_approval":
            pending = {
                "status": "waiting_approval",
                "actionId": action["id"],
                "question": "本次操作会产生额外付费重试，是否继续？",
            }
            update_run_context(self.journal.path, run_id, {"pendingApproval": pending})
            return pending
        del tool_call_id
        return self._execute_action(
            authorization,
            action,
            run_id=run_id,
            conversation=conversation,
        )

    def _execute_action(
        self,
        authorization: PolicyAuthorization,
        action: dict[str, Any],
        *,
        run_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            self.journal.charge_action_cost(action["id"])
            self.journal.mark_action_running(action["id"])
            result = self.composite_tools.execute(
                authorization.tool_name,
                authorization.arguments,
                run_id=run_id,
                action_id=action["id"],
                conversation=conversation,
            )
        except Exception as exc:
            self.journal.fail_action(
                action["id"],
                error_code=getattr(exc, "code", "agent_action_failed"),
                error_message=str(exc),
                retryable=False,
            )
            failure = {
                "status": "failed",
                "tool": authorization.tool_name,
                "errorCode": getattr(exc, "code", "agent_action_failed"),
                "error": str(exc),
            }
            self.journal.record_observation(
                run_id,
                action_id=action["id"],
                kind="action_terminal",
                state="failed",
                payload=failure,
                terminal=True,
            )
            update_run_context(self.journal.path, run_id, {"finalText": failure["error"]})
            return failure
        status = str(result.get("status") or "completed")
        if status == "pending":
            self.journal.wait_for_runtime(action["id"], result)
            return result
        self.journal.complete_action(action["id"], result)
        self.journal.record_observation(
            run_id,
            action_id=action["id"],
            kind="action_terminal",
            state=status,
            payload=result,
            terminal=True,
        )
        if status == "waiting_user":
            set_run_waiting_user(
                self.journal.path,
                run_id,
                code="agent_waiting_user",
                message=str(result.get("question") or "等待用户确认。"),
            )
        return result

    def _apply_user_message(
        self,
        run_id: str,
        context: dict[str, Any],
        message: str,
        *,
        planning_input: str | None = None,
    ) -> None:
        user_message = str(message or "").strip()
        effective_planning_input = str(
            planning_input if planning_input is not None else message or ""
        ).strip()
        updates: dict[str, Any] = {
            "latestUserMessage": user_message[:5000],
            "planningInput": effective_planning_input[:64000],
        }
        if not context.get("objective"):
            updates["objective"] = user_message[:5000]
        pending_question = context.get("pendingUserQuestion")
        if pending_question:
            updates["lastUserAnswer"] = user_message[:2000]
            if not (
                isinstance(pending_question, dict)
                and pending_question.get("reason") == "runtime_checkpoint"
            ):
                updates["pendingUserQuestion"] = None
                set_run_queued(self.journal.path, run_id)
        update_run_context(self.journal.path, run_id, updates)

    def _planned_video_count(self, run_id: str) -> int | None:
        plan = latest_successful_action_output(
            self.journal.path,
            run_id,
            ("review_video_plan", "prepare_video_plan"),
        )
        if not plan:
            return None
        try:
            count = int(plan.get("videoCount") or 0)
        except (TypeError, ValueError):
            return None
        return count if count > 0 else None

    def _conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        messages = self.store.list_messages(conversation_id)[-10:]
        return [
            {
                "role": item["role"] if item["role"] in {"user", "assistant"} else "user",
                "content": str(item.get("content") or "")[:5000],
                "timestamp": self._timestamp_ms(item.get("createdAt")),
            }
            for item in messages
            if str(item.get("content") or "").strip()
        ]

    @staticmethod
    def _decision_prompt(snapshot: dict[str, Any]) -> str:
        return (
            "根据下面的服务端状态选择唯一下一步。若已经交付完成，直接回复最终结果，不再调用工具。\n"
            + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        )

    @staticmethod
    def _idempotency_key(run_id: str, name: str, arguments: dict[str, Any]) -> str:
        digest = stable_payload_hash({"tool": name, "arguments": arguments})[:24]
        return f"{run_id}:{name}:{digest}"

    @staticmethod
    def _authorization_from_action(action: dict[str, Any]) -> PolicyAuthorization:
        return PolicyAuthorization(
            tool_name=action["toolName"],
            arguments=action.get("input") or {},
            side_effects=bool(action.get("sideEffects")),
            replay_safe=bool(action.get("replaySafe")),
            requires_approval=False,
            cost_units=float(action.get("costUnits") or 0),
        )

    @staticmethod
    def _timestamp_ms(value: Any) -> int:
        text = str(value or "")
        try:
            from datetime import datetime

            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return 0

    def _run_lock(self, run_id: str) -> RLock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(run_id, RLock())


_MAIN_AGENTS: dict[Path, AI8VideoMainAgent] = {}
_MAIN_AGENTS_LOCK = Lock()


def get_main_agent(store: ConversationStore, journal: AgentJournal) -> AI8VideoMainAgent:
    with _MAIN_AGENTS_LOCK:
        controller = _MAIN_AGENTS.get(store.path)
        if controller is None:
            controller = AI8VideoMainAgent(store, journal)
            _MAIN_AGENTS[store.path] = controller
        return controller
