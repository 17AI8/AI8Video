"""智能修图的图片模型调用与结果读取路由。"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from bottle import HTTPResponse, request, response, static_file

from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.reference_image_preprocessor import (
    TRANSFORMED_REFERENCE_DIR,
    ReferenceImagePreprocessor,
    build_smart_image_edit_prompt,
    remove_transformed_reference_asset,
)
from ai8video.integrations.llm_provider import build_openai_compat_llm


SMART_IMAGE_MAX_BYTES = 30 * 1024 * 1024
SMART_IMAGE_PROJECT_MAX_BYTES = 120 * 1024 * 1024
SMART_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SMART_IMAGE_MASK_EXTENSIONS = {".png", ".webp"}
SMART_IMAGE_PROJECT_PATH = TRANSFORMED_REFERENCE_DIR.parent / "智能修图画布.json"


def _clean_optimized_smart_image_prompt(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^(?:优化后的?(?:修图)?(?:提示词|描述|要求)?|修图(?:提示词|描述|要求))\s*[：:]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'“”")[:2000]
    if not text:
        raise RuntimeError("文本模型没有返回可用的修图描述")
    return text


def optimize_smart_image_prompt(requirement: str, llm) -> str:
    source = re.sub(r"\s+", " ", str(requirement or "")).strip()[:2000]
    build_smart_image_edit_prompt(source)
    prompt = (
        "把下面的用户修图要求改写为一段可直接提交给图片编辑模型的中文描述。"
        "要求具体、自然、可执行，说明应提升什么、必须保留什么、避免什么；"
        "不要添加用户未提出的人物、商品、场景或文字，不得要求移除、遮挡或弱化水印、署名、版权、Logo 或品牌标识。"
        "只输出优化后的单段描述，不要标题、解释、Markdown、引号或编号。\n"
        f"用户修图要求：{source or '自然提升画面质感，保持主体和构图不变。'}"
    )
    optimized = _clean_optimized_smart_image_prompt(llm(prompt))
    build_smart_image_edit_prompt(optimized)
    return optimized


def _read_smart_image_upload() -> tuple[str, bytes]:
    upload = request.files.get("file")
    if upload is None:
        raise ValueError("请选择要修图的图片")
    source_name = resolve_upload_filename(upload)
    suffix = Path(source_name).suffix.lower()
    if not source_name or suffix not in SMART_IMAGE_EXTENSIONS:
        raise ValueError("仅支持 JPG、PNG 或 WebP 图片")
    payload = upload.file.read(SMART_IMAGE_MAX_BYTES + 1)
    if not payload:
        raise ValueError("上传的图片为空")
    if len(payload) > SMART_IMAGE_MAX_BYTES:
        raise ValueError("图片超过 30 MB，请先压缩后再试")
    return source_name, payload


def _read_smart_image_mask() -> tuple[str, bytes] | None:
    upload = request.files.get("mask")
    if upload is None:
        return None
    source_name = resolve_upload_filename(upload)
    suffix = Path(source_name).suffix.lower()
    if not source_name or suffix not in SMART_IMAGE_MASK_EXTENSIONS:
        raise ValueError("局部蒙版仅支持 PNG 或 WebP")
    payload = upload.file.read(SMART_IMAGE_MAX_BYTES + 1)
    if not payload:
        raise ValueError("局部蒙版为空")
    if len(payload) > SMART_IMAGE_MAX_BYTES:
        raise ValueError("局部蒙版超过 30 MB")
    return source_name, payload


def _validate_smart_image_result(path: str) -> Path:
    output = Path(path).expanduser().resolve()
    root = TRANSFORMED_REFERENCE_DIR.resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("图片模型返回了无效的输出路径") from exc
    if output.suffix.lower() not in SMART_IMAGE_EXTENSIONS or not output.is_file():
        raise RuntimeError("图片模型没有返回可用的修图结果")
    return output


def api_render_smart_image():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        source_name, payload = _read_smart_image_upload()
        mask_upload = _read_smart_image_mask()
        prompt = str(request.forms.get("prompt") or "").strip()
        max_concurrency = max(1, min(8, int(request.forms.get("maxConcurrency") or 1)))
        config = AI8VideoConfig.from_env()
        with tempfile.TemporaryDirectory(prefix="ai8video-smart-image-") as tempdir:
            source_path = Path(tempdir) / f"source{Path(source_name).suffix.lower()}"
            source_path.write_bytes(payload)
            editor = ReferenceImagePreprocessor(config)
            if mask_upload:
                mask_name, mask_payload = mask_upload
                mask_path = Path(tempdir) / f"mask{Path(mask_name).suffix.lower()}"
                mask_path.write_bytes(mask_payload)
                result_path = editor.edit_image_with_mask(
                    str(source_path), str(mask_path), custom_prompt=prompt,
                    max_concurrency=max_concurrency,
                )
            else:
                result_path = editor.edit_image(
                    str(source_path), custom_prompt=prompt, max_concurrency=max_concurrency,
                )
            output = _validate_smart_image_result(
                result_path
            )
        display_name = f"{Path(source_name).stem[:80] or '图片'}-AI修图{output.suffix.lower()}"
        return {
            "ok": True,
            "resultUrl": f"/smart-image-results/{output.name}",
            "fileName": display_name,
            "model": config.image_model,
        }
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        response.status = 502
        return {"ok": False, "error": f"图片模型调用失败：{exc}"}


def api_optimize_smart_image_prompt():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        payload = request.json or {}
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("请先填写一句修图要求")
        config = AI8VideoConfig.from_env()
        if not config.has_llm():
            raise ValueError("请先在设置中配置文本模型")
        llm = build_openai_compat_llm(
            config,
            timeout_seconds=max(90, int(config.timeout_seconds or 0)),
            system_prompt="你是专业图片后期提示词编辑器，只改写用户要求，不执行图片编辑。",
            stream=False,
            transport_retry_count=1,
        )
        if llm is None:
            raise ValueError("请先在设置中配置文本模型")
        return {"ok": True, "prompt": optimize_smart_image_prompt(prompt, llm), "model": config.llm_model}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        response.status = 502
        return {"ok": False, "error": f"提示词优化失败：{exc}"}


def api_smart_image_result(filename: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_name = Path(str(filename or "")).name
    target = (TRANSFORMED_REFERENCE_DIR / clean_name).resolve()
    try:
        target.relative_to(TRANSFORMED_REFERENCE_DIR.resolve())
    except ValueError:
        response.status = 404
        return {"ok": False, "error": "修图结果不存在"}
    if not clean_name.startswith("reference-i2i-") or not target.is_file():
        response.status = 404
        return {"ok": False, "error": "修图结果不存在"}
    file_response = static_file(clean_name, root=str(TRANSFORMED_REFERENCE_DIR))
    file_response.set_header("Cache-Control", "no-store")
    return file_response


def api_delete_smart_image_result(filename: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_name = Path(str(filename or "")).name
    source = str(TRANSFORMED_REFERENCE_DIR / clean_name)
    if not clean_name.startswith("reference-i2i-") or not remove_transformed_reference_asset(source):
        response.status = 404
        return {"ok": False, "error": "修图结果不存在"}
    return {"ok": True}


def _smart_image_source_session_key(source: object) -> str:
    if not isinstance(source, dict):
        return ""
    source_key = str(source.get("sourceKey") or "").strip()
    if source_key:
        return source_key
    relative_path = str(source.get("sourceRelativePath") or "").strip().replace("\\", "/").lstrip("/")
    return f"library:{relative_path}" if relative_path else ""


def _smart_image_active_session(project: dict) -> dict:
    source = project.get("source") if isinstance(project.get("source"), dict) else {}
    session = {
        "sourceName": source.get("sourceName"),
        "sourceRelativePath": source.get("sourceRelativePath"),
        "sourceEdits": source.get("edits") or {},
    }
    for field in (
        "results",
        "jobs",
        "selectedJobId",
        "selectedResultId",
        "deletedResultKeys",
        "deletedJobIds",
        "selectedPresetId",
        "prompt",
        "batchCount",
        "viewMode",
        "comparePosition",
    ):
        if field in project:
            session[field] = project[field]
    return session


def _smart_image_result_key(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("url") or item.get("id") or "").strip()


def _smart_image_job_key(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or "").strip()


def _smart_image_string_values(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or len(text) > 1000 or text in seen:
            continue
        values.append(text)
        seen.add(text)
    return values[-limit:]


def _smart_image_recent_library_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    latest_by_path: dict[str, dict[str, object]] = {}
    for order, item in enumerate(value):
        if isinstance(item, str):
            path = item
            selected_at = ""
        elif isinstance(item, dict):
            path = str(item.get("path") or "")
            selected_at = str(item.get("selectedAt") or "")[:64]
        else:
            continue
        path = path.strip().replace("\\", "/").lstrip("/")[:1000]
        if not path:
            continue
        candidate = {"path": path, "selectedAt": selected_at, "order": order}
        current = latest_by_path.get(path)
        if current is None or selected_at > str(current.get("selectedAt") or ""):
            latest_by_path[path] = candidate
    ordered = sorted(
        latest_by_path.values(),
        key=lambda item: (str(item.get("selectedAt") or ""), -int(item.get("order") or 0)),
        reverse=True,
    )[:6]
    return [{"path": str(item["path"]), "selectedAt": str(item["selectedAt"])} for item in ordered]


def _merge_smart_image_recent_library_history(current: object, incoming: object) -> list[dict[str, str]]:
    return _smart_image_recent_library_history(
        _smart_image_recent_library_history(current) + _smart_image_recent_library_history(incoming)
    )


def _merge_smart_image_session(current: object, incoming: object) -> dict:
    current_session = dict(current) if isinstance(current, dict) else {}
    incoming_session = dict(incoming) if isinstance(incoming, dict) else {}
    deleted_result_keys = list(dict.fromkeys(
        _smart_image_string_values(current_session.get("deletedResultKeys"), 256)
        + _smart_image_string_values(incoming_session.get("deletedResultKeys"), 256)
    ))[-256:]
    deleted_job_ids = list(dict.fromkeys(
        _smart_image_string_values(current_session.get("deletedJobIds"), 128)
        + _smart_image_string_values(incoming_session.get("deletedJobIds"), 128)
    ))[-128:]
    deleted_result_set = set(deleted_result_keys)
    deleted_job_set = set(deleted_job_ids)
    current_results = [
        item for item in current_session.get("results") or []
        if isinstance(item, dict) and _smart_image_result_key(item) not in deleted_result_set
    ]
    incoming_results = [
        item for item in incoming_session.get("results") or []
        if isinstance(item, dict) and _smart_image_result_key(item) not in deleted_result_set
    ]
    current_jobs = [
        item for item in current_session.get("jobs") or []
        if isinstance(item, dict) and _smart_image_job_key(item) not in deleted_job_set
    ]
    incoming_jobs = [
        item for item in incoming_session.get("jobs") or []
        if isinstance(item, dict) and _smart_image_job_key(item) not in deleted_job_set
    ]
    current_result_keys = {_smart_image_result_key(item) for item in current_results}
    incoming_result_keys = {_smart_image_result_key(item) for item in incoming_results}
    current_job_ids = {_smart_image_job_key(item) for item in current_jobs}
    incoming_job_ids = {_smart_image_job_key(item) for item in incoming_jobs}
    incoming_is_missing_current_data = bool(
        current_result_keys - incoming_result_keys or current_job_ids - incoming_job_ids
    )
    merged = {**current_session, **incoming_session}
    if incoming_is_missing_current_data:
        merged["results"] = current_results + [
            item for item in incoming_results if _smart_image_result_key(item) not in current_result_keys
        ]
        merged["jobs"] = current_jobs + [
            item for item in incoming_jobs if _smart_image_job_key(item) not in current_job_ids
        ]
        for field in (
            "selectedJobId",
            "selectedResultId",
            "selectedPresetId",
            "prompt",
            "batchCount",
            "viewMode",
            "comparePosition",
            "sourceEdits",
            "updatedAt",
        ):
            if field in current_session:
                merged[field] = current_session[field]
    else:
        merged["results"] = incoming_results
        merged["jobs"] = incoming_jobs
    merged["deletedResultKeys"] = deleted_result_keys
    merged["deletedJobIds"] = deleted_job_ids
    valid_job_ids = [_smart_image_job_key(item) for item in merged["jobs"] if _smart_image_job_key(item)]
    valid_result_ids = [str(item.get("id") or "").strip() for item in merged["results"] if item.get("id")]
    selected_job_id = str(merged.get("selectedJobId") or "").strip()
    if selected_job_id not in valid_job_ids:
        current_selected_job_id = str(current_session.get("selectedJobId") or "").strip()
        merged["selectedJobId"] = (
            current_selected_job_id if current_selected_job_id in valid_job_ids else (valid_job_ids[-1] if valid_job_ids else "")
        )
    selected_result_id = str(merged.get("selectedResultId") or "").strip()
    if selected_result_id not in valid_result_ids:
        current_selected_result_id = str(current_session.get("selectedResultId") or "").strip()
        merged["selectedResultId"] = (
            current_selected_result_id
            if current_selected_result_id in valid_result_ids
            else (valid_result_ids[0] if valid_result_ids else "")
        )
    return merged


def _smart_image_project_sessions(project: dict) -> dict[str, dict]:
    sessions = {
        str(source_key): dict(session)
        for source_key, session in (project.get("sourceSessions") or {}).items()
        if source_key and isinstance(session, dict)
    }
    source_key = _smart_image_source_session_key(project.get("source"))
    if source_key:
        sessions[source_key] = _merge_smart_image_session(sessions.get(source_key), _smart_image_active_session(project))
    return sessions


def _merge_smart_image_project_payloads(current: dict, incoming: dict) -> dict:
    current_project = current.get("project") if isinstance(current.get("project"), dict) else {}
    incoming_project = incoming.get("project") if isinstance(incoming.get("project"), dict) else {}
    current_sessions = _smart_image_project_sessions(current_project)
    incoming_sessions = _smart_image_project_sessions(incoming_project)
    merged_sessions = dict(current_sessions)
    for source_key, session in incoming_sessions.items():
        merged_sessions[source_key] = _merge_smart_image_session(current_sessions.get(source_key), session)
    incoming_project["sourceSessions"] = merged_sessions
    incoming_project["recentLibraryHistory"] = _merge_smart_image_recent_library_history(
        current_project.get("recentLibraryHistory"),
        incoming_project.get("recentLibraryHistory"),
    )
    active_key = _smart_image_source_session_key(incoming_project.get("source"))
    active_session = merged_sessions.get(active_key)
    if active_session:
        for field in (
            "results",
            "jobs",
            "selectedJobId",
            "selectedResultId",
            "deletedResultKeys",
            "deletedJobIds",
            "selectedPresetId",
            "prompt",
            "batchCount",
            "viewMode",
            "comparePosition",
        ):
            if field in active_session:
                incoming_project[field] = active_session[field]
        if isinstance(incoming_project.get("source"), dict) and "sourceEdits" in active_session:
            incoming_project["source"]["edits"] = active_session["sourceEdits"]
    incoming["project"] = incoming_project
    return incoming


def api_smart_image_project():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        if not SMART_IMAGE_PROJECT_PATH.is_file():
            response.status = 404
            return {"ok": False, "error": "尚未保存智能修图工作台"}
        try:
            return json.loads(SMART_IMAGE_PROJECT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            response.status = 500
            return {"ok": False, "error": "智能修图工作台读取失败"}
    payload = request.body.read(SMART_IMAGE_PROJECT_MAX_BYTES + 1)
    if not payload or len(payload) > SMART_IMAGE_PROJECT_MAX_BYTES:
        response.status = 413
        return {"ok": False, "error": "智能修图工作台数据过大"}
    try:
        project = json.loads(payload.decode("utf-8"))
        if not isinstance(project, dict) or not isinstance(project.get("project"), dict):
            raise ValueError
        incoming_version = max(0, int(project.get("version") or 0))
        current_project: dict = {}
        if SMART_IMAGE_PROJECT_PATH.is_file():
            try:
                current_project = json.loads(SMART_IMAGE_PROJECT_PATH.read_text(encoding="utf-8"))
                current_version = max(0, int(current_project.get("version") or 0))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                current_version = 0
            if current_version > incoming_version:
                response.status = 409
                return {
                    "ok": False,
                    "error": "智能修图工作台已由新版页面更新，请刷新后重试",
                    "currentVersion": current_version,
                }
            if current_version == incoming_version and incoming_version >= 5:
                project = _merge_smart_image_project_payloads(current_project, project)
        SMART_IMAGE_PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=SMART_IMAGE_PROJECT_PATH.parent,
            prefix=".智能修图画布-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(project, handle, ensure_ascii=False)
            temporary = Path(handle.name)
        try:
            temporary.replace(SMART_IMAGE_PROJECT_PATH)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        response.status = 400
        return {"ok": False, "error": "智能修图工作台保存失败"}
    return {"ok": True}


def register_smart_image_editor_routes(app) -> None:
    app.route("/api/smart-image-editor/render", method=["POST", "OPTIONS"])(api_render_smart_image)
    app.route("/api/smart-image-editor/optimize-prompt", method=["POST", "OPTIONS"])(api_optimize_smart_image_prompt)
    app.route("/api/smart-image-editor/project", method=["GET", "PUT", "OPTIONS"])(api_smart_image_project)
    app.route("/smart-image-results/<filename>", method=["GET", "OPTIONS"])(api_smart_image_result)
    app.route("/api/smart-image-editor/results/<filename>", method=["DELETE", "OPTIONS"])(api_delete_smart_image_result)
