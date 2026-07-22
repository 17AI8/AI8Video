from __future__ import annotations

from pathlib import Path

from ai8video.core.paths import PROJECT_ROOT

USER_FILE_ROOT = (PROJECT_ROOT / "用户文件夹").resolve()
USER_MATERIAL_ROOT = (USER_FILE_ROOT / "用户素材").resolve()
USER_GENERATED_RESULT_ROOT = (USER_FILE_ROOT / "用户生成结果").resolve()
USER_BACKGROUND_MUSIC_DIR = (USER_FILE_ROOT / "背景音乐").resolve()
USER_RECYCLE_BIN_ROOT = (USER_FILE_ROOT / "回收站").resolve()
DEFAULT_USER_MATERIAL_ROOT = USER_MATERIAL_ROOT
DEFAULT_USER_GENERATED_RESULT_ROOT = USER_GENERATED_RESULT_ROOT
DEFAULT_BACKGROUND_MUSIC_DIR = USER_BACKGROUND_MUSIC_DIR


def ensure_user_file_root() -> Path:
    USER_FILE_ROOT.mkdir(parents=True, exist_ok=True)
    return USER_FILE_ROOT
