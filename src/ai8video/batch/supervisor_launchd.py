from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from ai8video.batch.batch_seed_file import resolve_batch_seed_file_path
from ai8video.core.config import AI8VideoConfig
from ai8video.core.paths import PROJECT_ROOT

DEFAULT_LABEL = "com.ai8.video.supervisor"


def project_root() -> Path:
    return PROJECT_ROOT


def default_launchd_plist_path(label: str = DEFAULT_LABEL) -> Path:
    override = (os.getenv("AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def default_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "AI8video"


def default_stdout_log_path(label: str = DEFAULT_LABEL) -> Path:
    return default_log_dir() / f"{label}.out.log"


def default_stderr_log_path(label: str = DEFAULT_LABEL) -> Path:
    return default_log_dir() / f"{label}.err.log"


def build_launchd_environment(config: AI8VideoConfig | None = None) -> dict[str, str]:
    config = config or AI8VideoConfig.from_env()
    env = {"PYTHONUNBUFFERED": "1"}
    for key, value in os.environ.items():
        if key.startswith("AI8VIDEO_") and value:
            env[key] = value
    env.setdefault("AI8VIDEO_DRY_RUN", "1" if config.dry_run else "0")
    return env


def build_program_arguments(
    *,
    config: AI8VideoConfig | None = None,
    python_executable: str | Path | None = None,
    seed_file: str | Path | None = None,
    schedule_times: str | None = None,
    target_pass_count: int | None = None,
    style_hint: str | None = None,
    poll_seconds: int = 30,
    min_pass_rate: float | None = None,
    consecutive_low_pass_runs: int | None = None,
    session_id: str = "launchd-batch-supervisor",
    source: str = "launchd",
    trigger: str = "launchd_batch_supervisor",
    refresh_runtime: bool = False,
) -> list[str]:
    config = config or AI8VideoConfig.from_env()
    default_seed_file, _ = resolve_batch_seed_file_path(config)
    seed_value = str(seed_file or config.batch_seed_file or default_seed_file).strip()
    if not seed_value:
        raise ValueError("seed_file is required for launchd deployment")
    schedule_value = str(schedule_times or config.batch_schedule_times or "").strip()
    if not schedule_value:
        raise ValueError("schedule_times is required for launchd deployment")
    normalized_schedule = ",".join(_parse_schedule_times(schedule_value))
    python_path = Path(python_executable or sys.executable).expanduser().resolve()
    args = [
        str(python_path),
        "-m",
        "ai8video.batch.daily_batch_supervisor",
        "--loop",
        "--seed-file",
        seed_value,
        "--schedule-times",
        normalized_schedule,
        "--target-pass-count",
        str(max(1, int(target_pass_count or config.batch_target_pass_count or 30))),
        "--poll-seconds",
        str(max(5, int(poll_seconds or 30))),
        "--min-pass-rate",
        str(float(min_pass_rate if min_pass_rate is not None else config.batch_alert_min_pass_rate)),
        "--consecutive-low-pass-runs",
        str(
            max(
                1,
                int(
                    consecutive_low_pass_runs
                    if consecutive_low_pass_runs is not None
                    else config.batch_alert_consecutive_low_pass_runs
                ),
            )
        ),
        "--session-id",
        session_id.strip() or "launchd-batch-supervisor",
        "--source",
        source.strip() or "launchd",
        "--trigger",
        trigger.strip() or "launchd_batch_supervisor",
    ]
    if style_hint or config.batch_style_hint:
        args.extend(["--style-hint", (style_hint or config.batch_style_hint or "").strip()])
    if refresh_runtime:
        args.append("--refresh-runtime")
    return args


def build_launchd_plist(
    *,
    config: AI8VideoConfig | None = None,
    label: str = DEFAULT_LABEL,
    python_executable: str | Path | None = None,
    plist_path: str | Path | None = None,
    seed_file: str | Path | None = None,
    schedule_times: str | None = None,
    target_pass_count: int | None = None,
    style_hint: str | None = None,
    poll_seconds: int = 30,
    min_pass_rate: float | None = None,
    consecutive_low_pass_runs: int | None = None,
    session_id: str = "launchd-batch-supervisor",
    source: str = "launchd",
    trigger: str = "launchd_batch_supervisor",
    refresh_runtime: bool = False,
) -> dict[str, Any]:
    config = config or AI8VideoConfig.from_env()
    target = Path(plist_path or default_launchd_plist_path(label)).expanduser()
    stdout_path = default_stdout_log_path(label)
    stderr_path = default_stderr_log_path(label)
    return {
        "Label": label,
        "ProgramArguments": build_program_arguments(
            config=config,
            python_executable=python_executable,
            seed_file=seed_file,
            schedule_times=schedule_times,
            target_pass_count=target_pass_count,
            style_hint=style_hint,
            poll_seconds=poll_seconds,
            min_pass_rate=min_pass_rate,
            consecutive_low_pass_runs=consecutive_low_pass_runs,
            session_id=session_id,
            source=source,
            trigger=trigger,
            refresh_runtime=refresh_runtime,
        ),
        "WorkingDirectory": str(project_root()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": build_launchd_environment(config),
        "_targetPlistPath": str(target),
    }


def write_launchd_plist(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    default_log_dir().mkdir(parents=True, exist_ok=True)
    plist_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    with target.open("wb") as fh:
        plistlib.dump(plist_payload, fh, sort_keys=True)
    return target


def inspect_launchd_deployment(
    *,
    plist_path: str | Path | None = None,
    label: str = DEFAULT_LABEL,
) -> dict[str, Any]:
    target = Path(plist_path or default_launchd_plist_path(label)).expanduser()
    payload: dict[str, Any] = {
        "manager": "launchd",
        "platformSupported": sys.platform == "darwin",
        "label": label,
        "plistPath": str(target),
        "exists": target.exists(),
        "loaded": False,
        "workingDirectory": str(project_root()),
        "stdoutPath": str(default_stdout_log_path(label)),
        "stderrPath": str(default_stderr_log_path(label)),
        "scheduleTimes": [],
    }
    if target.exists():
        try:
            loaded = plistlib.loads(target.read_bytes())
        except Exception as exc:
            payload["readError"] = str(exc)
            loaded = {}
        if isinstance(loaded, dict):
            payload["label"] = str(loaded.get("Label") or label)
            payload["workingDirectory"] = str(loaded.get("WorkingDirectory") or payload["workingDirectory"])
            payload["stdoutPath"] = str(loaded.get("StandardOutPath") or payload["stdoutPath"])
            payload["stderrPath"] = str(loaded.get("StandardErrorPath") or payload["stderrPath"])
            program_args = [str(item) for item in loaded.get("ProgramArguments") or []]
            payload["programArguments"] = program_args
            payload["seedFile"] = _extract_flag_value(program_args, "--seed-file")
            schedule_text = _extract_flag_value(program_args, "--schedule-times")
            payload["scheduleTimes"] = _parse_schedule_times(schedule_text or "") if schedule_text else []
            payload["targetPassCount"] = _extract_flag_int(program_args, "--target-pass-count")
            payload["styleHint"] = _extract_flag_value(program_args, "--style-hint")
            payload["pollSeconds"] = _extract_flag_int(program_args, "--poll-seconds")
            payload["minPassRate"] = _extract_flag_float(program_args, "--min-pass-rate")
            payload["consecutiveLowPassRuns"] = _extract_flag_int(program_args, "--consecutive-low-pass-runs")
    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        payload["domain"] = domain
        try:
            result = subprocess.run(
                ["launchctl", "print", f"{domain}/{payload['label']}"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload["loaded"] = True
            text = (result.stdout or "").strip()
            if text:
                payload["launchctlSummary"] = text.splitlines()[0][:240]
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            if detail:
                payload["launchctlError"] = detail.splitlines()[0][:240]
    return payload


def install_launchd_service(path: str | Path, *, label: str = DEFAULT_LABEL) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("launchd deployment is only supported on macOS")
    target = Path(path).expanduser().resolve()
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(target)], check=False, capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return inspect_launchd_deployment(plist_path=target, label=label)


def uninstall_launchd_service(path: str | Path, *, label: str = DEFAULT_LABEL, delete_plist: bool = True) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    payload = inspect_launchd_deployment(plist_path=target, label=label)
    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(target)], check=False, capture_output=True, text=True)
    if delete_plist:
        target.unlink(missing_ok=True)
    payload["removed"] = True
    payload["exists"] = target.exists()
    payload["loaded"] = False
    return payload


def _extract_flag_value(program_args: list[str], flag: str) -> str | None:
    for index, value in enumerate(program_args):
        if value == flag and index + 1 < len(program_args):
            text = str(program_args[index + 1]).strip()
            if text:
                return text
    return None


def _extract_flag_int(program_args: list[str], flag: str) -> int | None:
    value = _extract_flag_value(program_args, flag)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_flag_float(program_args: list[str], flag: str) -> float | None:
    value = _extract_flag_value(program_args, flag)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_schedule_times(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = list(value)
    parsed: list[str] = []
    for item in raw_items:
        for chunk in str(item or "").split(","):
            text = chunk.strip()
            if not text:
                continue
            hour_text, sep, minute_text = text.partition(":")
            if sep != ":":
                raise ValueError(f"invalid schedule time: {text}")
            hour = int(hour_text)
            minute = int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"invalid schedule time: {text}")
            normalized = f"{hour:02d}:{minute:02d}"
            if normalized not in parsed:
                parsed.append(normalized)
    return sorted(parsed)


def _add_write_like_arguments(parser: argparse.ArgumentParser, config: AI8VideoConfig) -> None:
    parser.add_argument("--plist-path", type=str, default=str(default_launchd_plist_path()))
    parser.add_argument("--label", type=str, default=DEFAULT_LABEL)
    parser.add_argument("--python", dest="python_executable", type=str, default=sys.executable)
    parser.add_argument("--seed-file", type=str, default=config.batch_seed_file or "")
    parser.add_argument("--schedule-times", type=str, default=config.batch_schedule_times)
    parser.add_argument("--target-pass-count", type=int, default=config.batch_target_pass_count)
    parser.add_argument("--style-hint", type=str, default=config.batch_style_hint or "")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--min-pass-rate", type=float, default=config.batch_alert_min_pass_rate)
    parser.add_argument(
        "--consecutive-low-pass-runs",
        type=int,
        default=config.batch_alert_consecutive_low_pass_runs,
    )
    parser.add_argument("--session-id", type=str, default="launchd-batch-supervisor")
    parser.add_argument("--source", type=str, default="launchd")
    parser.add_argument("--trigger", type=str, default="launchd_batch_supervisor")
    parser.add_argument("--refresh-runtime", action="store_true")


def main() -> int:
    config = AI8VideoConfig.from_env()
    parser = argparse.ArgumentParser(description="AI8video 批量短视频 launchd 部署工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="输出 launchd plist 内容")
    _add_write_like_arguments(render_parser, config)

    write_parser = subparsers.add_parser("write", help="写入 launchd plist，但不加载")
    _add_write_like_arguments(write_parser, config)

    install_parser = subparsers.add_parser("install", help="写入并加载 launchd plist")
    _add_write_like_arguments(install_parser, config)

    uninstall_parser = subparsers.add_parser("uninstall", help="卸载 launchd plist")
    uninstall_parser.add_argument("--plist-path", type=str, default=str(default_launchd_plist_path()))
    uninstall_parser.add_argument("--label", type=str, default=DEFAULT_LABEL)
    uninstall_parser.add_argument("--keep-plist", action="store_true")

    status_parser = subparsers.add_parser("status", help="查看当前 launchd 部署状态")
    status_parser.add_argument("--plist-path", type=str, default=str(default_launchd_plist_path()))
    status_parser.add_argument("--label", type=str, default=DEFAULT_LABEL)

    args = parser.parse_args()

    if args.command == "status":
        print(json.dumps(inspect_launchd_deployment(plist_path=args.plist_path, label=args.label), ensure_ascii=False, indent=2))
        return 0

    if args.command == "uninstall":
        payload = uninstall_launchd_service(
            args.plist_path,
            label=args.label,
            delete_plist=not args.keep_plist,
        )
        print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
        return 0

    payload = build_launchd_plist(
        config=config,
        label=args.label,
        python_executable=args.python_executable,
        plist_path=args.plist_path,
        seed_file=args.seed_file,
        schedule_times=args.schedule_times,
        target_pass_count=args.target_pass_count,
        style_hint=args.style_hint.strip() or None,
        poll_seconds=args.poll_seconds,
        min_pass_rate=args.min_pass_rate,
        consecutive_low_pass_runs=args.consecutive_low_pass_runs,
        session_id=args.session_id,
        source=args.source,
        trigger=args.trigger,
        refresh_runtime=args.refresh_runtime,
    )
    target_path = write_launchd_plist(args.plist_path, payload)

    if args.command == "render":
        print(target_path.read_text(encoding="utf-8"))
        return 0

    if args.command == "install":
        status = install_launchd_service(target_path, label=args.label)
        print(json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2))
        return 0

    status = inspect_launchd_deployment(plist_path=target_path, label=args.label)
    print(json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
