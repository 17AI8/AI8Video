from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


IMAGE_HOST_SETTINGS_DIR = (USER_FILE_ROOT / "图床").resolve()
IMAGE_HOST_SETTINGS_PATH = IMAGE_HOST_SETTINGS_DIR / "settings.json"
BUILT_IN_MJJ_ID = "mjj-today"


class ImageHostError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageHostProvider:
    id: str
    name: str
    upload_url: str
    file_field: str = "file"
    response_url_path: str = "data.url"
    auth_header: str = ""
    auth_token: str = ""


@dataclass(frozen=True)
class ImageHostSettings:
    selected_provider_id: str = ""
    custom_providers: tuple[ImageHostProvider, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "selectedProviderId": self.selected_provider_id,
            "providers": [_public_provider(item) for item in available_providers(self)],
        }


def load_image_host_settings() -> ImageHostSettings:
    try:
        payload = json.loads(IMAGE_HOST_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    return normalize_image_host_settings(payload if isinstance(payload, dict) else {})


def save_image_host_settings(payload: dict[str, Any]) -> ImageHostSettings:
    current = load_image_host_settings()
    providers = _normalize_custom_providers(payload.get("providers"), current)
    selected = str(payload.get("selectedProviderId") or "").strip()
    valid_ids = {BUILT_IN_MJJ_ID, *(item.id for item in providers)}
    if selected not in valid_ids:
        selected = ""
    settings = ImageHostSettings(selected_provider_id=selected, custom_providers=providers)
    ensure_user_file_root()
    IMAGE_HOST_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_HOST_SETTINGS_PATH.write_text(
        json.dumps(_settings_payload(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings


def normalize_image_host_settings(payload: dict[str, Any]) -> ImageHostSettings:
    providers = _normalize_custom_providers(payload.get("providers"), ImageHostSettings())
    selected = str(payload.get("selected_provider_id") or payload.get("selectedProviderId") or "").strip()
    valid_ids = {BUILT_IN_MJJ_ID, *(item.id for item in providers)}
    return ImageHostSettings(selected if selected in valid_ids else "", providers)


def available_providers(settings: ImageHostSettings) -> tuple[ImageHostProvider, ...]:
    built_in = ImageHostProvider(
        id=BUILT_IN_MJJ_ID,
        name="MJJ.TODAY（内置）",
        upload_url="https://mjj.today/json",
        file_field="source",
        response_url_path="image.url",
    )
    return (built_in, *settings.custom_providers)


def upload_reference_image(source: str, *, timeout_seconds: int = 30) -> str:
    settings = load_image_host_settings()
    if not settings.selected_provider_id:
        raise ImageHostError("视频模型不接受本地参考图；请先在设置的“图床”页选择图床。")
    provider = next(
        (item for item in available_providers(settings) if item.id == settings.selected_provider_id),
        None,
    )
    if provider is None:
        raise ImageHostError("当前选择的图床已不存在，请重新选择。")
    content, mime, filename = _read_image_source(source)
    if provider.id == BUILT_IN_MJJ_ID:
        return _upload_to_mjj(content, mime, filename, timeout_seconds)
    return _upload_to_custom(provider, content, mime, filename, timeout_seconds)


def _upload_to_mjj(content: bytes, mime: str, filename: str, timeout_seconds: int) -> str:
    session = requests.Session()
    home = session.get("https://mjj.today/", timeout=timeout_seconds)
    home.raise_for_status()
    match = re.search(r'PF\.obj\.config\.auth_token\s*=\s*"([^"]+)"', home.text)
    if not match:
        raise ImageHostError("MJJ.TODAY 未返回匿名上传令牌。")
    data = {"action": "upload", "type": "file", "auth_token": match.group(1)}
    response = session.post(
        "https://mjj.today/json",
        data=data,
        files={"source": (filename, content, mime)},
        timeout=timeout_seconds,
    )
    return _read_upload_response(response, "image.url", fallback_paths=("success.image.url", "image.display_url"))


def _upload_to_custom(
    provider: ImageHostProvider,
    content: bytes,
    mime: str,
    filename: str,
    timeout_seconds: int,
) -> str:
    headers = {}
    if provider.auth_header and provider.auth_token:
        headers[provider.auth_header] = provider.auth_token
    response = requests.post(
        provider.upload_url,
        headers=headers,
        files={provider.file_field: (filename, content, mime)},
        timeout=timeout_seconds,
    )
    return _read_upload_response(response, provider.response_url_path)


def _read_upload_response(
    response: requests.Response,
    path: str,
    *,
    fallback_paths: tuple[str, ...] = (),
) -> str:
    if not response.ok:
        excerpt = " ".join((response.text or "").split())[:300]
        raise ImageHostError(f"图床上传失败：HTTP {response.status_code}，{excerpt}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ImageHostError("图床没有返回 JSON。") from exc
    for candidate in (path, *fallback_paths):
        value: Any = payload
        for key in candidate.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        url = str(value or "").strip()
        if url.startswith(("https://", "http://")):
            return url
    raise ImageHostError(f"图床响应中未找到有效图片 URL：{path}")


def _read_image_source(source: str) -> tuple[bytes, str, str]:
    text = str(source or "").strip()
    if text.startswith("data:"):
        match = re.match(r"^data:([^;,]+);base64,(.+)$", text, flags=re.DOTALL)
        if not match:
            raise ImageHostError("参考图 data URL 格式无效。")
        mime = match.group(1)
        return base64.b64decode(match.group(2)), mime, f"reference.{_extension_for_mime(mime)}"
    path = Path(text).expanduser()
    if not path.is_file():
        raise ImageHostError("找不到需要上传的参考图文件。")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return path.read_bytes(), mime, path.name


def _normalize_custom_providers(value: Any, current: ImageHostSettings) -> tuple[ImageHostProvider, ...]:
    if not isinstance(value, list):
        return current.custom_providers
    old_tokens = {item.id: item.auth_token for item in current.custom_providers}
    providers: list[ImageHostProvider] = []
    for index, item in enumerate(value[:20]):
        if not isinstance(item, dict):
            continue
        provider_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(item.get("id") or f"custom-{index + 1}"))[:64]
        upload_url = str(item.get("uploadUrl") or item.get("upload_url") or "").strip()
        if not upload_url.startswith(("https://", "http://")):
            continue
        token = str(item.get("authToken") or item.get("auth_token") or "").strip()
        if not token and item.get("hasAuthToken"):
            token = old_tokens.get(provider_id, "")
        providers.append(ImageHostProvider(
            id=provider_id,
            name=str(item.get("name") or provider_id).strip()[:80],
            upload_url=upload_url,
            file_field=str(item.get("fileField") or item.get("file_field") or "file").strip()[:64],
            response_url_path=str(item.get("responseUrlPath") or item.get("response_url_path") or "data.url").strip()[:160],
            auth_header=str(item.get("authHeader") or item.get("auth_header") or "").strip()[:80],
            auth_token=token,
        ))
    return tuple(providers)


def _public_provider(provider: ImageHostProvider) -> dict[str, Any]:
    payload = asdict(provider)
    payload.pop("auth_token", None)
    payload["hasAuthToken"] = bool(provider.auth_token)
    payload["builtIn"] = provider.id == BUILT_IN_MJJ_ID
    payload["privacyRisk"] = provider.id == BUILT_IN_MJJ_ID
    return {
        "id": payload["id"], "name": payload["name"], "uploadUrl": payload["upload_url"],
        "fileField": payload["file_field"], "responseUrlPath": payload["response_url_path"],
        "authHeader": payload["auth_header"], "hasAuthToken": payload["hasAuthToken"],
        "builtIn": payload["builtIn"], "privacyRisk": payload["privacyRisk"],
    }


def _settings_payload(settings: ImageHostSettings) -> dict[str, Any]:
    return {
        "selected_provider_id": settings.selected_provider_id,
        "providers": [asdict(item) for item in settings.custom_providers],
    }


def _extension_for_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"
    }.get(mime, "png")
