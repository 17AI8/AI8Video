"""AI8video 的短视频生成、媒体处理与任务编排能力。"""

__version__ = "0.3.0"

from ai8video.core.identity import ENV_PREFIX, PRODUCT_NAME, PRODUCT_SLUG, bridge_legacy_environment

bridge_legacy_environment()

from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.batch.batch_alert_store import BatchAlertStore
from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.batch.daily_batch_runner import DailyBatchRunner
from ai8video.generation.pipeline import AI8VideoPipeline

__all__ = [
    "BatchAlertStore",
    "BatchReportStore",
    "DailyBatchRunner",
    "ENV_PREFIX",
    "AI8VideoConversationController",
    "AI8VideoPipeline",
    "PRODUCT_NAME",
    "PRODUCT_SLUG",
    "__version__",
]
