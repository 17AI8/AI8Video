"""Restart reconciliation for Agent runs waiting on deterministic Runtime work."""

from __future__ import annotations

from typing import Any

from ai8video.application.agent_context import (
    get_run_context,
    set_run_waiting_runtime,
    set_run_waiting_user,
    update_run_context,
)
from ai8video.application.agent_responses import agent_error_response


class AgentRuntimeRecoveryMixin:
    def _reconcile_waiting_runtime(self, run_id: str) -> bool:
        run = self.journal.get_run(run_id)
        context = get_run_context(self.journal.path, run_id)
        pending_question = context.get("pendingUserQuestion")
        checkpoint_waiting = (
            run["state"] == "waiting_user"
            and isinstance(pending_question, dict)
            and pending_question.get("reason") == "runtime_checkpoint"
        )
        if run["state"] != "waiting_runtime" and not checkpoint_waiting:
            return False
        generation_batch_id = str(context.get("generationBatchId") or "").strip()
        action_id = str(run.get("pendingActionId") or context.get("activeActionId") or "").strip()
        if not generation_batch_id or not action_id:
            return False
        from ai8video.application.facade import get_chat_status

        try:
            status = get_chat_status(run["conversationId"], generation_batch_id)
        except Exception:
            return False
        if status.get("phase") == "awaiting_tail_frame_continue":
            question = "检测到可恢复的尾帧接力检查点，请在生成结果卡片中确认继续后再返回本对话。"
            set_run_waiting_user(
                self.journal.path,
                run_id,
                code="agent_runtime_checkpoint_waiting_user",
                message=question,
            )
            update_run_context(
                self.journal.path,
                run_id,
                {
                    "pendingUserQuestion": {"question": question, "reason": "runtime_checkpoint"},
                    "runtimeCheckpointObserved": True,
                },
            )
            return False
        progress = status.get("generationProgress") if isinstance(status.get("generationProgress"), dict) else {}
        read_only_recovery = bool(status.get("readOnlyRecovery"))
        checkpoint_observed = checkpoint_waiting or bool(context.get("runtimeCheckpointObserved"))
        if self._runtime_progress_terminal(status, progress) and (
            read_only_recovery or checkpoint_observed
        ):
            observation = self._recovered_generation_observation(status, progress)
            observation["recoveredAfterRestart"] = read_only_recovery
            observation["checkpointReconciled"] = checkpoint_observed
            output = {
                "status": "completed",
                "recoveredAfterRestart": read_only_recovery,
                "checkpointReconciled": checkpoint_observed,
                "generationBatchId": generation_batch_id,
                "generationProgress": progress,
                "resultReconciliation": status.get("resultReconciliation"),
            }
            self.journal.complete_action(action_id, output)
            self.journal.record_observation(
                run_id,
                action_id=action_id,
                kind=(
                    "generation_terminal_recovered"
                    if read_only_recovery
                    else "generation_terminal_checkpoint"
                ),
                state=observation["status"],
                payload=observation,
                terminal=True,
            )
            update_run_context(
                self.journal.path,
                run_id,
                {
                    "generationStatus": observation["status"],
                    "generatedVideoCount": observation["successCount"],
                    "failedVideoCount": observation["failedCount"],
                    "latestGenerationObservation": observation,
                    "runtimeRecoveredAfterRestart": read_only_recovery,
                    "runtimeCheckpointReconciled": checkpoint_observed,
                    "runtimeCheckpointObserved": False,
                    "pendingUserQuestion": None,
                },
            )
            return True
        if checkpoint_waiting and not read_only_recovery:
            set_run_waiting_runtime(self.journal.path, run_id)
            update_run_context(
                self.journal.path,
                run_id,
                {"pendingUserQuestion": None, "runtimeCheckpointObserved": True},
            )
            return False
        if not read_only_recovery:
            return False
        self._fail_lost_runtime_worker(run, action_id, generation_batch_id, progress)
        return False

    def _fail_lost_runtime_worker(
        self,
        run: dict[str, Any],
        action_id: str,
        generation_batch_id: str,
        progress: dict[str, Any],
    ) -> None:
        message = (
            "服务进程重启后，任务账本仍显示未完成，但当前进程没有对应 Runtime worker。"
            "为避免重复扣费或伪造续跑，本轮已停止；已有产物和审计记录均保留。"
        )
        failure = {
            "status": "failed",
            "generationBatchId": generation_batch_id,
            "errorCode": "agent_runtime_worker_lost",
            "error": message,
            "recoveredProgress": progress,
        }
        self.journal.record_observation(
            run["id"],
            action_id=action_id,
            kind="generation_runtime_lost",
            state="failed",
            payload=failure,
            terminal=True,
        )
        self.journal.fail_action(
            action_id,
            error_code="agent_runtime_worker_lost",
            error_message=message,
            retryable=False,
        )
        conversation = self.store.get_conversation(run["conversationId"])
        latest_response = agent_error_response(
            self.journal,
            conversation,
            run["id"],
            "agent_runtime_worker_lost",
            message,
        )
        update_run_context(
            self.journal.path,
            run["id"],
            {
                "generationStatus": "failed",
                "latestGenerationObservation": failure,
                "runtimeRecoveredAfterRestart": True,
                "latestResponse": latest_response,
            },
        )

    @staticmethod
    def _runtime_progress_terminal(status: dict[str, Any], progress: dict[str, Any]) -> bool:
        terminal = {"completed", "completed_with_error", "failed", "cancelled", "canceled"}
        if str(progress.get("status") or "").strip() in terminal:
            return True
        ledger = status.get("ledgerSnapshot") if isinstance(status.get("ledgerSnapshot"), dict) else {}
        if str(ledger.get("status") or "").strip() in terminal:
            return True
        items = [item for item in progress.get("items") or [] if isinstance(item, dict)]
        item_terminal = {"succeeded", "failed", "skipped", "deleted", "cancelled", "canceled"}
        return bool(items) and all(str(item.get("status") or "").strip() in item_terminal for item in items)

    @staticmethod
    def _recovered_generation_observation(
        status: dict[str, Any],
        progress: dict[str, Any],
    ) -> dict[str, Any]:
        items = [_normalized_recovered_item(item) for item in progress.get("items") or [] if isinstance(item, dict)]
        items = [item for item in items if item["videoIndex"] > 0]
        success_count = sum(item["status"] == "succeeded" for item in items)
        reported_success = int(progress.get("succeededCount") or 0)
        success_count = max(success_count, reported_success)
        reported_failed = int(progress.get("failedCount") or 0) + int(progress.get("skippedCount") or 0)
        total = max(int(progress.get("totalRequested") or 0), len(items), success_count + reported_failed)
        failed_count = max(reported_failed, total - success_count)
        state = "succeeded" if success_count and not failed_count else (
            "partial_success" if success_count else "failed"
        )
        assets = [item["assetRecord"] for item in items if isinstance(item.get("assetRecord"), dict)]
        failures = [item for item in items if item["status"] != "succeeded"]
        return {
            "action": "generate_video_batch",
            "status": state,
            "generationBatchId": status.get("generationBatchId") or progress.get("generationBatchId"),
            "requestedCount": total,
            "succeededCount": success_count,
            "successCount": success_count,
            "failedCount": failed_count,
            "totalCount": total,
            "failures": failures,
            "items": items,
            "assets": assets,
            "recoveredAfterRestart": True,
        }


def _normalized_recovered_item(item: dict[str, Any]) -> dict[str, Any]:
    video_index = int(item.get("videoIndex") or item.get("video_index") or item.get("index") or 0)
    raw_status = str(item.get("status") or "failed").strip().lower()
    succeeded = raw_status in {"succeeded", "completed"}
    return {
        "videoIndex": video_index,
        "status": "succeeded" if succeeded else "failed",
        "retryable": False if succeeded else item.get("retryable") is not False,
        "reason": str(item.get("reason") or item.get("error") or "").strip(),
        "jobId": item.get("jobId") or item.get("job_id"),
        "assetRecord": item.get("assetRecord") if isinstance(item.get("assetRecord"), dict) else None,
    }
