from __future__ import annotations

VIDEO_POSTPROCESS_CODEC = "libx264"
VIDEO_POSTPROCESS_PRESET = "veryfast"
VIDEO_POSTPROCESS_CRF = "16"
VIDEO_POSTPROCESS_PIX_FMT = "yuv420p"


def video_postprocess_encoding_meta() -> dict[str, str]:
    return {
        "codec": VIDEO_POSTPROCESS_CODEC,
        "preset": VIDEO_POSTPROCESS_PRESET,
        "crf": VIDEO_POSTPROCESS_CRF,
        "pixFmt": VIDEO_POSTPROCESS_PIX_FMT,
    }


def append_video_postprocess_encoding_args(cmd: list[str], *, include_pix_fmt: bool = True) -> None:
    cmd.extend(
        [
            "-c:v",
            VIDEO_POSTPROCESS_CODEC,
            "-preset",
            VIDEO_POSTPROCESS_PRESET,
            "-crf",
            VIDEO_POSTPROCESS_CRF,
        ]
    )
    if include_pix_fmt:
        cmd.extend(["-pix_fmt", VIDEO_POSTPROCESS_PIX_FMT])
