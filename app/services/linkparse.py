"""从一段文本里识别网盘分享链接与提取码。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 每家网盘的分享链接特征（顺序即识别优先级）
DRIVE_PATTERNS: dict[str, tuple[str, ...]] = {
    "115": (
        r"https?://(?:[\w.-]*\.)?115(?:cdn)?\.com/s/[0-9a-zA-Z]+",
        r"https?://(?:[\w.-]*\.)?anxia\.com/s/[0-9a-zA-Z]+",
    ),
    "baidu": (r"https?://pan\.baidu\.com/s/[0-9a-zA-Z_\-]+",),
    "quark": (
        r"https?://pan\.quark\.cn/s/[0-9a-zA-Z]+",
        r"https?://quark\.cn/s/[0-9a-zA-Z]+",
    ),
    "xunlei": (r"https?://pan\.xunlei\.com/s/[0-9a-zA-Z_\-]+",),
    "mock": (r"https?://mockdrive\.test/s/[0-9a-zA-Z]+",),
}

PASSCODE_PATTERNS = (
    r"[?&]pwd=([0-9a-zA-Z]{4})",
    r"提取码[：:\s]*([0-9a-zA-Z]{4})",
    r"访问码[：:\s]*([0-9a-zA-Z]{4})",
    r"密码[：:\s]*([0-9a-zA-Z]{4})",
    r"口令[：:\s]*([0-9a-zA-Z]{4})",
    r"[（(]([0-9a-zA-Z]{4})[)）]\s*$",
)

_URL_TAIL = re.compile(r"[)\]】>,，。；;\"'”’\s]+$")


@dataclass
class FoundLink:
    drive: str
    url: str
    share_id: str = ""
    passcode: str = ""
    raw: str = ""
    extra: dict = field(default_factory=dict)


def _detect_drive(text: str) -> str | None:
    for drive, patterns in DRIVE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                return drive
    return None


def extract_passcode(text: str) -> str:
    for pattern in PASSCODE_PATTERNS:
        hit = re.search(pattern, text, re.I)
        if hit:
            return hit.group(1)
    return ""


def _share_id_from_url(url: str) -> str:
    hit = re.search(r"/s/([0-9a-zA-Z_\-]+)", url)
    return hit.group(1) if hit else ""


def parse_links(text: str, *, limit: int = 5) -> list[FoundLink]:
    """解析文本中的网盘链接（同一链接只保留一条）。"""
    if not text:
        return []
    results: list[FoundLink] = []
    seen: set[tuple[str, str]] = set()

    for drive, patterns in DRIVE_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                url = _URL_TAIL.sub("", match.group(0))
                key = (drive, url.lower().rstrip("/"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    FoundLink(
                        drive=drive,
                        url=url,
                        share_id=_share_id_from_url(url),
                        passcode=extract_passcode(text),
                    )
                )

    # 按在原文中出现的顺序排序，更符合直觉
    def position(link: FoundLink) -> int:
        idx = text.find(link.url)
        return idx if idx >= 0 else 10**9

    results.sort(key=position)
    return results[:limit]


def looks_like_share_text(text: str) -> bool:
    return _detect_drive(text) is not None
