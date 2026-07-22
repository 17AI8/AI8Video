from __future__ import annotations

import html
import re
from html.parser import HTMLParser


ALLOWED_TAGS = {
    "div", "span", "p", "h1", "h2", "h3", "strong", "em", "small",
    "svg", "g", "path", "circle", "ellipse", "line", "polyline", "polygon", "rect",
}
ALLOWED_HTML_ATTRS = {
    "id", "class", "aria-hidden", "data-role", "viewbox", "d", "fill", "stroke",
    "stroke-width", "stroke-linecap", "stroke-linejoin", "cx", "cy", "r", "rx", "ry", "xmlns",
    "x", "y", "x1", "x2", "y1", "y2", "width", "height", "points", "style",
    "opacity", "fill-opacity", "fill-rule", "clip-rule", "stroke-opacity", "stroke-dasharray",
    "stroke-dashoffset", "stroke-miterlimit", "vector-effect", "paint-order", "shape-rendering",
    "transform", "transform-origin", "preserveaspectratio", "pathlength",
}
BLOCKED_CSS_PATTERNS = (
    r"url\s*\(", r"@import", r"@font-face", r"javascript\s*:", r"expression\s*\(",
    r"position\s*:\s*fixed", r"(?:^|[,{])\s*(?:html|body|#root|\.clip|\.hf-scene)\b",
    r"animation(?:-[\w-]+)?\s*:", r"transition(?:-[\w-]+)?\s*:", r"content\s*:",
    r"behavior\s*:", r"-moz-binding\s*:",
)
BLOCKED_INLINE_STYLE_PATTERNS = tuple(
    pattern for pattern in BLOCKED_CSS_PATTERNS if "#root" not in pattern
)
STYLE_ATTRIBUTE_PATTERN = re.compile(r"\sstyle\s*=\s*(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
HOST_LAYOUT_PROPERTIES = {
    "position", "left", "right", "top", "bottom", "width", "height", "min-width", "max-width",
    "min-height", "max-height", "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left", "display", "float",
    "clear", "flex", "flex-direction", "flex-wrap", "grid", "grid-template", "align-items",
    "justify-content", "place-items", "overflow", "font-size", "line-height", "white-space", "transform",
    "z-index",
}


class FragmentInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.element_count = 0
        self.text_fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in ALLOWED_TAGS:
            raise ValueError(f"HTML 动效不允许标签 {normalized_tag}")
        self.element_count += 1
        for name, value in attrs:
            self._validate_attribute(name.lower(), value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", str(data or "")).strip()
        if text:
            self.text_fragments.append(text)

    def _validate_attribute(self, name: str, value: str | None) -> None:
        if name.startswith("on") or name not in ALLOWED_HTML_ATTRS:
            raise ValueError(f"HTML 动效不允许属性 {name}")
        validate_attribute_value(name, value)
        if name == "id":
            self._record_id(value)
        if name == "style":
            validate_inline_style(value)

    def _record_id(self, value: str | None) -> None:
        identifier = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{1,63}", identifier):
            raise ValueError("HTML 动效元素 id 不合法")
        if identifier in self.ids:
            raise ValueError("HTML 动效元素 id 重复")
        self.ids.add(identifier)


def inspect_fragment(fragment: str) -> FragmentInspector:
    inspector = FragmentInspector()
    inspector.feed(fragment)
    inspector.close()
    return inspector


def sanitize_inline_styles(fragment: str) -> str:
    return STYLE_ATTRIBUTE_PATTERN.sub(_sanitize_style_attribute, fragment)


def normalize_scene_text(fragment: str, dialogue_text: str | None) -> str:
    if dialogue_text is None:
        return fragment
    rewriter = _SceneTextRewriter(_dialogue_phrase(dialogue_text))
    rewriter.feed(fragment)
    rewriter.close()
    return "".join(rewriter.parts)


class _SceneTextRewriter(HTMLParser):
    def __init__(self, replacement: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.replacement = replacement
        self.dialogue_key = _visible_text_key(replacement)
        self.visible_text_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(_format_tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(_format_tag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not data.strip():
            self.parts.append(data)
            return
        if self.visible_text_count >= 1:
            return
        if self.replacement:
            self.parts.append(html.escape(self.replacement, quote=False))
            self.visible_text_count += 1


def _format_tag(tag: str, attrs: list[tuple[str, str | None]], *, closed: bool = False) -> str:
    values = [name if value is None else f'{name}="{html.escape(value, quote=True)}"' for name, value in attrs]
    suffix = " /" if closed else ""
    return f"<{tag}{(' ' + ' '.join(values)) if values else ''}{suffix}>"


def _dialogue_phrase(value: str) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    phrase = next((item.strip() for item in re.split(r"[。！？!?；;，,]+", compact) if item.strip()), "")
    return phrase[:6]


def _visible_text_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _sanitize_style_attribute(match: re.Match[str]) -> str:
    declarations = (item.strip() for item in match.group(2).split(";"))
    safe = [
        item for item in declarations
        if item and not _is_host_layout_declaration(item) and not css_has_blocked_pattern(item, inline=True)
    ]
    return f' style="{";".join(safe)}"' if safe else ""


def is_host_layout_property(name: str) -> bool:
    return name.strip().lower() in HOST_LAYOUT_PROPERTIES


def _is_host_layout_declaration(value: str) -> bool:
    name, separator, _ = value.partition(":")
    return bool(separator) and is_host_layout_property(name)


def css_has_blocked_pattern(value: str, *, inline: bool = False) -> bool:
    patterns = BLOCKED_INLINE_STYLE_PATTERNS if inline else BLOCKED_CSS_PATTERNS
    lowered = value.lower()
    return any(re.search(pattern, lowered, flags=re.MULTILINE) for pattern in patterns)


def validate_inline_style(value: str | None) -> None:
    style = str(value or "").strip()
    if not style or len(style) > 2_000:
        raise ValueError("HTML 动效元素 style 为空或过长")
    if css_has_blocked_pattern(style, inline=True):
        raise ValueError("HTML 动效元素 style 包含不安全样式")


def validate_attribute_value(name: str, value: str | None) -> None:
    if name == "style":
        return
    normalized = str(value or "").strip().lower()
    if re.search(r"url\s*\(|(?:javascript|data|https?|file)\s*:", normalized):
        raise ValueError(f"HTML 动效属性 {name} 包含外部或可执行资源")
