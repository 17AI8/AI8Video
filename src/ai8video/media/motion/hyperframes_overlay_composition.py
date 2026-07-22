"""兼容旧导入路径；实际 composition 编译位于 renderer 模块。"""

from ai8video.media.motion.hyperframes_overlay_renderer import (
    build_composition_html,
    build_motion_manifest,
)

__all__ = ["build_composition_html", "build_motion_manifest"]
