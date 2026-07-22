from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
import re
from typing import Any
import zipfile

from ai8video.assets.user_files import (
    USER_MATERIAL_ROOT,
    ensure_user_file_root,
)

USER_IMAGE_MATERIAL_DIR = (USER_MATERIAL_ROOT / "图片素材库").resolve()
USER_SCRIPT_MATERIAL_DIR = (USER_MATERIAL_ROOT / "剧本素材库").resolve()
USER_FLOWER_WATERMARK_DIR = (USER_MATERIAL_ROOT / "花字水印库").resolve()
IMAGE_MATERIAL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SCRIPT_MATERIAL_EXTENSIONS = {".txt", ".md", ".docx"}


def ensure_user_material_dirs() -> None:
    ensure_user_file_root()
    USER_IMAGE_MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    USER_SCRIPT_MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    USER_FLOWER_WATERMARK_DIR.mkdir(parents=True, exist_ok=True)


def material_dir(kind: str) -> Path:
    normalized = str(kind or "").strip().lower()
    if normalized in {"image", "images", "图片", "图片素材库"}:
        return USER_IMAGE_MATERIAL_DIR
    if normalized in {"script", "scripts", "剧本", "剧本素材库"}:
        return USER_SCRIPT_MATERIAL_DIR
    if normalized in {
        "flower-watermark",
        "flower_watermark",
        "watermark",
        "watermarks",
        "花字水印",
        "花字水印库",
        "水印",
        "水印库",
    }:
        return USER_FLOWER_WATERMARK_DIR
    return USER_MATERIAL_ROOT


def list_user_materials() -> dict[str, Any]:
    ensure_user_material_dirs()
    images = _list_material_files(USER_IMAGE_MATERIAL_DIR, kind="image", extensions=IMAGE_MATERIAL_EXTENSIONS)
    scripts = _list_material_files(USER_SCRIPT_MATERIAL_DIR, kind="script", extensions=SCRIPT_MATERIAL_EXTENSIONS)
    flower_watermarks = _list_material_files(
        USER_FLOWER_WATERMARK_DIR,
        kind="flower-watermark",
        extensions=IMAGE_MATERIAL_EXTENSIONS,
    )
    return {
        "root": str(USER_MATERIAL_ROOT),
        "imageDir": str(USER_IMAGE_MATERIAL_DIR),
        "scriptDir": str(USER_SCRIPT_MATERIAL_DIR),
        "flowerWatermarkDir": str(USER_FLOWER_WATERMARK_DIR),
        "images": images,
        "scripts": scripts,
        "flowerWatermarks": flower_watermarks,
        "imageCount": len(images),
        "scriptCount": len(scripts),
        "flowerWatermarkCount": len(flower_watermarks),
    }


def list_script_material_sources() -> list[dict[str, Any]]:
    ensure_user_material_dirs()
    return _list_material_files(
        USER_SCRIPT_MATERIAL_DIR,
        kind="script",
        extensions=SCRIPT_MATERIAL_EXTENSIONS,
        include_script_preview=False,
    )


def delete_user_material(kind: str, relative_path: str) -> dict[str, Any]:
    root = _validated_material_root(kind)
    clean_path = _normalize_material_relative_path(relative_path)
    target = (root / clean_path).resolve()
    if not _is_within(root, target):
        raise ValueError("relativePath is outside material library")
    allowed_extensions = _allowed_extensions_for_root(root)
    if allowed_extensions and target.suffix.lower() not in allowed_extensions:
        raise ValueError("unsupported material extension")
    if not target.is_file():
        raise FileNotFoundError("material not found")
    target.unlink()
    return {
        "ok": True,
        "kind": _kind_for_root(root),
        "deleted": {
            "name": target.name,
            "relativePath": target.relative_to(root).as_posix(),
            "path": str(target),
        },
    }


def resolve_material_mentions(text: str) -> dict[str, Any]:
    mentions = _extract_mentions(text)
    if not mentions:
        return {"mentions": [], "images": [], "scripts": [], "missing": []}
    materials = list_user_materials()
    images = materials["images"]
    scripts = materials["scripts"]
    resolved_images: list[dict[str, Any]] = []
    resolved_scripts: list[dict[str, Any]] = []
    missing: list[str] = []
    for mention in mentions:
        image = _find_material(mention, images)
        script = _find_material(mention, scripts)
        if image:
            resolved_images.append(image)
        if script:
            resolved_scripts.append(script)
        if not image and not script:
            missing.append(mention)
    return {
        "mentions": mentions,
        "images": _dedupe_by_path(resolved_images),
        "scripts": _dedupe_by_path(resolved_scripts),
        "missing": missing,
    }


def expand_material_mentions(text: str) -> tuple[str, dict[str, Any]]:
    resolved = resolve_material_mentions(text)
    additions: list[str] = []
    images = resolved.get("images") or []
    scripts = resolved.get("scripts") or []
    if images:
        additions.append("参考图路径：\n" + "\n".join(str(image["path"]) for image in images))
    for script in scripts:
        content = read_script_material_text(script["path"], limit=None)
        if content:
            script["contentPreview"] = re.sub(r"\s+", " ", content).strip()[:180]
            script["contentCharCount"] = len(content)
            additions.append(f"@{script['name']} 剧本素材内容：\n{content}")
    if not additions:
        return text, resolved
    return text.rstrip() + "\n\n" + "\n\n".join(additions), resolved


def read_script_material_text(path: str | Path, *, limit: int | None = 5000) -> str:
    return _read_script_text(Path(path), limit=limit)


def _list_material_files(
    root: Path,
    *,
    kind: str,
    extensions: set[str],
    include_script_preview: bool = True,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if extensions and path.suffix.lower() not in extensions:
            continue
        try:
            stat = path.stat()
            relative = path.relative_to(root).as_posix()
        except OSError:
            continue
        item = {
            "name": path.name,
            "stem": path.stem,
            "relativePath": relative,
            "path": str(path),
            "sizeBytes": stat.st_size,
            "modifiedAt": stat.st_mtime,
            "kind": kind,
        }
        if kind == "image":
            item["url"] = f"/user-materials/images/{relative}"
        elif kind == "flower-watermark":
            item["url"] = f"/user-materials/flower-watermarks/{relative}"
        elif include_script_preview:
            item["preview"] = _read_script_text(path, limit=180)
        items.append(item)
    items.sort(key=lambda item: item.get("modifiedAt") or 0, reverse=True)
    return items


def _read_text(path: Path, *, limit: int | None) -> str:
    try:
        return _apply_text_limit(path.read_text(encoding="utf-8", errors="ignore").strip(), limit)
    except OSError:
        return ""


def _read_script_text(path: Path, *, limit: int | None) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx_text(path, limit=limit)
    return _read_text(path, limit=limit)


def _read_docx_text(path: Path, *, limit: int | None) -> str:
    try:
        from docx import Document
    except ImportError:
        return _read_docx_xml_text(path, limit=limit)
    try:
        document = Document(str(path))
    except Exception:
        return _read_docx_xml_text(path, limit=limit)
    parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    text = "\n".join(parts).strip()
    if text:
        return _apply_text_limit(text, limit)
    return _read_docx_xml_text(path, limit=limit)


def _read_docx_xml_text(path: Path, *, limit: int | None) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name for name in archive.namelist()
                if name.startswith("word/") and name.endswith(".xml")
                and any(part in name for part in ("/document.xml", "/header", "/footer", "/footnotes", "/endnotes"))
            ]
            chunks: list[str] = []
            for name in names:
                chunks.extend(_extract_word_xml_text(archive.read(name)))
                if limit is not None and sum(len(chunk) for chunk in chunks) >= limit:
                    break
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    text = re.sub(r"[ \t\r\f\v]+", " ", "\n".join(chunks))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return _apply_text_limit(text, limit)


def _apply_text_limit(text: str, limit: int | None) -> str:
    if limit is None:
        return text
    return text[:limit]


def _extract_word_xml_text(payload: bytes) -> list[str]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append(" ")
            elif node.tag == f"{namespace}br":
                parts.append("\n")
        line = re.sub(r"[ \t\r\f\v]+", " ", "".join(parts)).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def _extract_mentions(text: str) -> list[str]:
    raw_mentions = re.findall(r"@([^\s@，。；;：:、,]+)", text)
    result: list[str] = []
    for item in raw_mentions:
        cleaned = item.strip().strip("()（）[]【】")
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _find_material(mention: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = _normalize_name(mention)
    for item in items:
        if _normalize_name(str(item.get("name") or "")) == normalized:
            return item
    for item in items:
        if _normalize_name(str(item.get("relativePath") or "")) == normalized:
            return item
    for item in items:
        if "." not in mention and _normalize_name(str(item.get("stem") or "")) == normalized:
            return item
    for item in items:
        stem = _normalize_name(str(item.get("stem") or ""))
        name = _normalize_name(str(item.get("name") or ""))
        if normalized and (normalized in stem or normalized in name):
            return item
    return None


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _dedupe_by_path(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        path = str(item.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(item)
    return result


def _validated_material_root(kind: str) -> Path:
    ensure_user_material_dirs()
    root = material_dir(kind).resolve()
    if root == USER_IMAGE_MATERIAL_DIR:
        return USER_IMAGE_MATERIAL_DIR
    if root == USER_SCRIPT_MATERIAL_DIR:
        return USER_SCRIPT_MATERIAL_DIR
    if root == USER_FLOWER_WATERMARK_DIR:
        return USER_FLOWER_WATERMARK_DIR
    raise ValueError("unsupported material kind")


def _normalize_material_relative_path(relative_path: str) -> str:
    raw_value = str(relative_path or "").strip().replace("\\", "/")
    if not raw_value:
        raise ValueError("relativePath is required")
    if raw_value.startswith("/") or Path(raw_value).is_absolute():
        raise ValueError("relativePath must be relative")
    return raw_value


def _allowed_extensions_for_root(root: Path) -> set[str]:
    if root == USER_IMAGE_MATERIAL_DIR:
        return IMAGE_MATERIAL_EXTENSIONS
    if root == USER_SCRIPT_MATERIAL_DIR:
        return SCRIPT_MATERIAL_EXTENSIONS
    if root == USER_FLOWER_WATERMARK_DIR:
        return IMAGE_MATERIAL_EXTENSIONS
    return set()


def _kind_for_root(root: Path) -> str:
    if root == USER_FLOWER_WATERMARK_DIR:
        return "flower-watermark"
    if root == USER_SCRIPT_MATERIAL_DIR:
        return "script"
    return "image"


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False
