from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.core.config import AI8VideoConfig
from ai8video.media.motion.html_motion_overlay import default_html_motion_overlay_enabled
from ai8video.application.message_parser import parse_employee_message
from ai8video.core.models import EpisodePrompt, ParsedRequest, PipelineResult
from ai8video.generation.pipeline import AI8VideoPipeline


@dataclass
class BatchTask:
    message: str
    source_message: str
    attempt: int = 0
    request: ParsedRequest | None = None
    episode: EpisodePrompt | None = None
    rewrite_instruction: str | None = None


class DailyBatchRunner:
    def __init__(
        self,
        pipeline: AI8VideoPipeline | None = None,
        config: AI8VideoConfig | None = None,
        target_pass_count: int = 30,
        initial_candidate_budget: int = 45,
        max_candidate_budget: int = 60,
        max_retries_per_video: int = 2,
        report_store: BatchReportStore | None = None,
    ):
        self.config = config or AI8VideoConfig.from_env()
        self.pipeline = pipeline or AI8VideoPipeline(self.config)
        self.target_pass_count = target_pass_count
        self.initial_candidate_budget = initial_candidate_budget
        self.max_candidate_budget = max_candidate_budget
        self.max_retries_per_video = max_retries_per_video
        self.report_store = report_store or BatchReportStore(self.config.batch_report_dir)

    def run(
        self,
        seed_messages: Iterable[str],
        *,
        style_hint: str | None = None,
        trigger: str = "manual",
        source: str = "manual",
        session_id: str | None = None,
    ) -> dict:
        seed_list = [item.strip() for item in seed_messages if item and item.strip()]
        prepared_seed_list = [_merge_seed_style(item, style_hint) for item in seed_list]
        html_motion_overlay_enabled = default_html_motion_overlay_enabled()
        queue: deque[BatchTask] = deque(BatchTask(message=item, source_message=item) for item in prepared_seed_list)
        known_seed_messages = list(prepared_seed_list)
        results: list[PipelineResult] = []
        total_video_attempts = 0
        success_count = 0
        failed_count = 0
        seeded_tasks_used = 0
        failure_reasons: Counter[str] = Counter()
        top_asset: dict | None = None
        total_cost = 0.0
        retry_scheduled_count = 0
        expansion_round_count = 0
        expanded_seed_count = 0
        expanded_seed_samples: list[str] = []
        top_up_strategies: list[str] = []
        expansion_error: str | None = None

        while total_video_attempts < self.max_candidate_budget and success_count < self.target_pass_count:
            if not queue:
                try:
                    expansion_strategy, expanded_messages = self._expand_batch_queue(
                        known_seed_messages=known_seed_messages,
                        style_hint=style_hint,
                        pass_count=success_count,
                        total_video_attempts=total_video_attempts,
                        failure_reasons=failure_reasons,
                    )
                except Exception as exc:
                    expansion_error = str(exc)
                    top_up_strategies.append("expansion_failed")
                    break
                if not expanded_messages:
                    break
                top_up_strategies.append(expansion_strategy)
                expansion_round_count += 1
                expanded_seed_count += len(expanded_messages)
                for item in expanded_messages:
                    if item not in expanded_seed_samples and len(expanded_seed_samples) < 8:
                        expanded_seed_samples.append(item)
                    queue.append(BatchTask(message=item, source_message=item))
                    known_seed_messages.append(item)
                continue

            task = queue.popleft()
            if task.attempt == 0:
                seeded_tasks_used += 1
            if task.request is not None and task.episode is not None and task.rewrite_instruction:
                result = self.pipeline.rewrite_episode(task.request, task.episode, task.rewrite_instruction)
            else:
                request = parse_employee_message(task.message)
                request.html_motion_overlay_enabled = html_motion_overlay_enabled
                result = self._run_new_request(request, task.message)
            results.append(result)

            for episode, outcome, asset in zip(result.episodes, result.outcomes, result.asset_records):
                total_video_attempts += 1
                if outcome.decision == "generated":
                    success_count += 1
                    if top_asset is None:
                        top_asset = asset
                else:
                    failed_count += 1
                    for reason in outcome.reasons:
                        failure_reasons[reason] += 1

                total_cost += _extract_usage_cost(asset.get("usage"))

                if total_video_attempts >= self.max_candidate_budget or success_count >= self.target_pass_count:
                    break

            if (
                total_video_attempts >= self.initial_candidate_budget
                and total_video_attempts < self.max_candidate_budget
                and success_count / max(1, total_video_attempts) < 0.7
            ):
                # Keep draining the remaining queue up to max_candidate_budget.
                pass

        report = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "dryRun": self.config.dry_run,
            "styleHint": style_hint,
            "htmlMotionOverlayEnabled": html_motion_overlay_enabled,
            "targetGenerationCount": self.target_pass_count,
            "targetPassCount": self.target_pass_count,
            "initialCandidateBudget": self.initial_candidate_budget,
            "maxCandidateBudget": self.max_candidate_budget,
            "maxRetriesPerVideo": self.max_retries_per_video,
            "seedMessages": len(seed_list),
            "seedMessageSamples": prepared_seed_list[:5],
            "seededTasksUsed": seeded_tasks_used,
            "totalVideoAttempts": total_video_attempts,
            "successCount": success_count,
            "failedCount": failed_count,
            "passCount": success_count,
            "retryCount": 0,
            "rejectCount": failed_count,
            "retryScheduledCount": retry_scheduled_count,
            "expansionRoundCount": expansion_round_count,
            "expandedSeedCount": expanded_seed_count,
            "expandedSeedSamples": expanded_seed_samples,
            "topUpStrategies": top_up_strategies,
            "expansionError": expansion_error,
            "successRate": round(success_count / max(1, total_video_attempts), 4),
            "passRate": round(success_count / max(1, total_video_attempts), 4),
            "averageCost": round(total_cost / max(1, total_video_attempts), 6),
            "totalCost": round(total_cost, 6),
            "topAsset": top_asset,
            "topFailureReasons": [
                {"reason": reason, "count": count}
                for reason, count in failure_reasons.most_common(8)
            ],
            "results": [result.to_dict() for result in results],
        }
        report["goalMet"] = success_count >= self.target_pass_count
        report["needsTopUpTo60"] = (
            total_video_attempts >= self.initial_candidate_budget
            and success_count / max(1, total_video_attempts) < 0.7
            and total_video_attempts < self.max_candidate_budget
        )
        if self.report_store is not None:
            saved_meta = self.report_store.save(
                report,
                trigger=trigger,
                source=source,
                session_id=session_id,
                style_hint=style_hint,
                seed_messages=prepared_seed_list,
            )
            report.update(saved_meta)
        return report

    def _run_new_request(self, request: ParsedRequest, message: str) -> PipelineResult:
        run_request = getattr(self.pipeline, "run_request", None)
        if callable(run_request):
            return run_request(request)
        return self.pipeline.run_from_message(message)

    def _expand_batch_queue(
        self,
        *,
        known_seed_messages: list[str],
        style_hint: str | None,
        pass_count: int,
        total_video_attempts: int,
        failure_reasons: Counter[str],
    ) -> tuple[str, list[str]]:
        remaining_budget = self.max_candidate_budget - total_video_attempts
        remaining_goal_gap = self.target_pass_count - pass_count
        if remaining_budget <= 0 or remaining_goal_gap <= 0:
            return "goal_met_or_budget_exhausted", []
        if not known_seed_messages:
            return "no_seed_messages", []

        current_pass_rate = pass_count / total_video_attempts if total_video_attempts else 1.0
        if total_video_attempts >= self.initial_candidate_budget and current_pass_rate < 0.7:
            expansion_target = remaining_budget
            strategy = "initial_pool_exhausted_low_pass_rate_top_up"
        else:
            expansion_target = min(remaining_budget, max(1, remaining_goal_gap))
            strategy = "queue_exhausted_goal_gap_top_up"

        failure_reason_list = [reason for reason, _ in failure_reasons.most_common(5)]
        expanded_messages = self.pipeline.expand_seed_messages(
            known_seed_messages,
            expansion_target,
            style_hint=style_hint,
            failure_reasons=failure_reason_list,
        )
        prepared_messages = [
            _merge_seed_style(item, style_hint)
            for item in expanded_messages
            if item and item.strip()
        ]
        deduped_messages: list[str] = []
        seen = set(known_seed_messages)
        for item in prepared_messages:
            if item in seen:
                continue
            seen.add(item)
            deduped_messages.append(item)
        return strategy, deduped_messages


def _build_retry_message(source_message: str, episode_prompt: str, reasons: list[str]) -> str:
    retry_reasons = "；".join(reason for reason in reasons if reason) or "画面质量不稳定"
    return (
        f"{source_message}\n"
        f"重做要求：沿用原目标，但重点修正以下问题：{retry_reasons}。"
        f"如果需要，以当前这条视频提示词为主进行优化：{episode_prompt}"
    )


def _build_retry_instruction(episode_prompt: str, reasons: list[str]) -> str:
    retry_reasons = "；".join(reason for reason in reasons if reason) or "画面质量不稳定"
    return (
        f"沿用原目标，只重做这一条视频。"
        f"重点修正：{retry_reasons}。"
        f"必要时参考当前提示词继续优化：{episode_prompt}"
    )


def _extract_usage_cost(usage: dict | None) -> float:
    if not isinstance(usage, dict):
        return 0.0
    cost = usage.get("cost")
    if isinstance(cost, dict):
        amount = cost.get("amount")
        if isinstance(amount, (int, float)):
            return float(amount)
    amount = usage.get("amount")
    if isinstance(amount, (int, float)):
        return float(amount)
    return 0.0


def _merge_seed_style(seed_message: str, style_hint: str | None) -> str:
    if not style_hint:
        return seed_message
    if any(token in seed_message for token in style_hint.split("、")):
        return seed_message
    suffix = f"风格更偏{style_hint}。"
    if seed_message.endswith(("。", "！", "？")):
        return seed_message + suffix
    return seed_message + "。" + suffix
