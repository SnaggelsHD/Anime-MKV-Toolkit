import re

# Common anime release patterns: "S01E05", "- 05 ", "E05", trailing "05v2".
_PATTERNS = [
    re.compile(r"[Ss]\d{1,2}[Ee](\d{1,4})"),
    re.compile(r"[Ee][Pp]?(\d{1,4})\b"),
    re.compile(r"-\s*(\d{1,4})(?:v\d)?\s*[\[\(]"),
    re.compile(r"-\s*(\d{1,4})(?:v\d)?\s*$"),
    re.compile(r"\b(\d{1,4})\b"),
]

_EXCLUDE = {480, 576, 720, 1080, 1440, 2160}


def parse_episode_number(filename: str, fallback: int) -> int:
    stem = filename.rsplit(".", 1)[0]
    for pattern in _PATTERNS:
        for match in pattern.finditer(stem):
            n = int(match.group(1))
            if n in _EXCLUDE or 1900 <= n <= 2100:
                continue
            return n
    return fallback


def render_title(template: str, n: int, episode: int) -> str:
    return template.replace("{n}", str(n)).replace("{episode}", str(episode))
