from __future__ import annotations

from pathlib import Path

from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.core.config import AI8VideoConfig
from ai8video.core.paths import PROJECT_ROOT

DEFAULT_RELATIVE_SEED_FILE = "media_resources/ai8video/batch_supervisor/seed_messages.txt"


def project_root() -> Path:
    return PROJECT_ROOT


def default_batch_seed_file_path() -> Path:
    return (project_root() / DEFAULT_RELATIVE_SEED_FILE).resolve()


def resolve_batch_seed_file_path(config: AI8VideoConfig | None = None) -> tuple[Path, str]:
    config = config or AI8VideoConfig.from_env()
    raw_path = str(config.batch_seed_file or "").strip()
    if raw_path:
        target = Path(raw_path).expanduser()
        if not target.is_absolute():
            target = (project_root() / target).resolve()
        else:
            target = target.resolve()
        return target, "config"
    return default_batch_seed_file_path(), "default"


def inspect_batch_seed_file(config: AI8VideoConfig | None = None, *, preview_limit: int = 5) -> dict:
    path, source = resolve_batch_seed_file_path(config)
    messages = read_batch_seed_file(path)
    return {
        "path": str(path),
        "source": source,
        "exists": path.exists() and path.is_file(),
        "lineCount": len(messages),
        "preview": messages[: max(1, int(preview_limit or 5))],
    }


def read_batch_seed_file(path: str | Path) -> list[str]:
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        return []
    return _dedupe_lines(target.read_text(encoding="utf-8").splitlines())


def write_batch_seed_file(
    messages: list[str],
    *,
    config: AI8VideoConfig | None = None,
    path: str | Path | None = None,
) -> dict:
    normalized = _dedupe_lines(messages)
    if not normalized:
        raise ValueError("seed messages is required")
    target = Path(path).expanduser().resolve() if path else resolve_batch_seed_file_path(config)[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(normalized) + "\n", encoding="utf-8")
    return inspect_batch_seed_file(
        AI8VideoConfig.from_env() if config is None and path is None else config,
    ) if path is None else {
        "path": str(target),
        "source": "manual",
        "exists": True,
        "lineCount": len(normalized),
        "preview": normalized[:5],
    }


def collect_seed_messages_from_recent_reports(
    report_store: BatchReportStore,
    *,
    report_limit: int = 8,
    max_messages: int = 40,
) -> tuple[list[str], list[str]]:
    collected: list[str] = []
    report_ids: list[str] = []
    seen: set[str] = set()
    for item in report_store.read_recent(limit=max(1, int(report_limit or 8))):
        report_id = str(item.get("reportId") or "").strip()
        samples = _dedupe_lines(item.get("seedMessageSamples") or [])
        used_this_report = False
        for sample in samples:
            if sample in seen:
                continue
            seen.add(sample)
            collected.append(sample)
            used_this_report = True
            if len(collected) >= max(1, int(max_messages or 40)):
                break
        if used_this_report and report_id:
            report_ids.append(report_id)
        if len(collected) >= max(1, int(max_messages or 40)):
            break
    return collected, report_ids


def build_batch_seed_file_from_recent_reports(
    *,
    config: AI8VideoConfig | None = None,
    report_store: BatchReportStore | None = None,
    report_limit: int = 8,
    max_messages: int = 40,
) -> dict:
    config = config or AI8VideoConfig.from_env()
    store = report_store or BatchReportStore(config.batch_report_dir)
    messages, report_ids = collect_seed_messages_from_recent_reports(
        store,
        report_limit=report_limit,
        max_messages=max_messages,
    )
    if not messages:
        raise ValueError("最近日报里还没有可用候选内容，暂时不能生成值守种子文件")
    payload = write_batch_seed_file(messages, config=config)
    payload["reportIds"] = report_ids
    payload["reportCount"] = len(report_ids)
    return payload


def _dedupe_lines(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned
