"""按文件名顺序组装浏览器工作台脚本。"""

from pathlib import Path


def workbench_script_paths(static_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted((static_dir / "scripts").glob("*.js")))


def read_workbench_script(static_dir: Path) -> str:
    paths = workbench_script_paths(static_dir)
    if not paths:
        raise FileNotFoundError("AI8video 工作台脚本分片不存在")
    return "".join(path.read_text(encoding="utf-8") for path in paths)
