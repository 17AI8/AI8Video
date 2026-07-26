"""智能修图的图片模型调用与结果读取路由。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bottle import HTTPResponse, request, response, static_file

from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.reference_image_preprocessor import (
    TRANSFORMED_REFERENCE_DIR,
    ReferenceImagePreprocessor,
    remove_transformed_reference_asset,
)


SMART_IMAGE_MAX_BYTES = 30 * 1024 * 1024
SMART_IMAGE_PROJECT_MAX_BYTES = 120 * 1024 * 1024
SMART_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SMART_IMAGE_MASK_EXTENSIONS = {".png", ".webp"}
SMART_IMAGE_PROJECT_PATH = TRANSFORMED_REFERENCE_DIR.parent / "智能修图画布.json"


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


def api_smart_image_project():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        if not SMART_IMAGE_PROJECT_PATH.is_file():
            response.status = 404
            return {"ok": False, "error": "尚未保存智能修图画布"}
        try:
            return json.loads(SMART_IMAGE_PROJECT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            response.status = 500
            return {"ok": False, "error": "智能修图画布读取失败"}
    payload = request.body.read(SMART_IMAGE_PROJECT_MAX_BYTES + 1)
    if not payload or len(payload) > SMART_IMAGE_PROJECT_MAX_BYTES:
        response.status = 413
        return {"ok": False, "error": "智能修图画布数据过大"}
    try:
        project = json.loads(payload.decode("utf-8"))
        if not isinstance(project, dict) or not isinstance(project.get("project"), dict):
            raise ValueError
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
        return {"ok": False, "error": "智能修图画布保存失败"}
    return {"ok": True}


def register_smart_image_editor_routes(app) -> None:
    app.route("/api/smart-image-editor/render", method=["POST", "OPTIONS"])(api_render_smart_image)
    app.route("/api/smart-image-editor/project", method=["GET", "PUT", "OPTIONS"])(api_smart_image_project)
    app.route("/smart-image-results/<filename>", method=["GET", "OPTIONS"])(api_smart_image_result)
    app.route("/api/smart-image-editor/results/<filename>", method=["DELETE", "OPTIONS"])(api_delete_smart_image_result)
