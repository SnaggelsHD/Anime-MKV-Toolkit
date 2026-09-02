"""Reading and writing Matroska chapters via mkvtoolnix (mkvextract/
mkvpropedit) for the chapter analyzer - no re-encoding involved, chapter
markers are container-level metadata. Backup/restore of chapters is
handled by the Toolkit's own backup/restore system (see app/backup.py and
app/restore.py), not duplicated here."""
import subprocess
import uuid
from pathlib import Path
from xml.sax.saxutils import escape


class ChapterToolError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def extract_chapters_xml(mkv_path: Path) -> str | None:
    """Return the existing chapters as XML text, or None if the file has
    no chapters."""
    result = _run(["mkvextract", str(mkv_path), "chapters", "-"])
    if result.returncode != 0:
        raise ChapterToolError(f"mkvextract failed: {result.stderr.strip()}")
    xml = result.stdout.strip()
    return xml if xml else None


CHAPTER_XML_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">
<Chapters>
  <EditionEntry>
    <EditionFlagDefault>1</EditionFlagDefault>
    <EditionFlagOrdered>0</EditionFlagOrdered>
"""
CHAPTER_XML_FOOTER = """  </EditionEntry>
</Chapters>
"""


def _fmt(seconds: float) -> str:
    seconds = max(seconds, 0)
    total_ns = round(seconds * 1_000_000_000)
    hours, rem = divmod(total_ns, 3_600_000_000_000)
    minutes, rem = divmod(rem, 60_000_000_000)
    secs, ns = divmod(rem, 1_000_000_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ns:09d}"


def build_chapters_xml(chapters: list[dict]) -> str:
    """chapters: list of {"title": str, "start": seconds}, sorted by start."""
    parts = [CHAPTER_XML_HEADER]
    for i, ch in enumerate(chapters, start=1):
        uid = 1000000000 + i
        parts.append("    <ChapterAtom>\n")
        parts.append(f"      <ChapterUID>{uid}</ChapterUID>\n")
        parts.append(f"      <ChapterTimeStart>{_fmt(ch['start'])}</ChapterTimeStart>\n")
        parts.append("      <ChapterDisplay>\n")
        parts.append(f"        <ChapterString>{escape(ch['title'])}</ChapterString>\n")
        parts.append("        <ChapterLanguage>eng</ChapterLanguage>\n")
        parts.append("      </ChapterDisplay>\n")
        parts.append("    </ChapterAtom>\n")
    parts.append(CHAPTER_XML_FOOTER)
    return "".join(parts)


def write_chapters(mkv_path: Path, chapters: list[dict]) -> None:
    xml = build_chapters_xml(chapters)
    tmp_path = mkv_path.with_suffix(mkv_path.suffix + f".chapters-{uuid.uuid4().hex[:8]}.xml")
    tmp_path.write_text(xml)
    try:
        result = _run(["mkvpropedit", str(mkv_path), "--chapters", str(tmp_path)])
        if result.returncode != 0:
            raise ChapterToolError(f"mkvpropedit write failed: {result.stderr.strip()}")
    finally:
        tmp_path.unlink(missing_ok=True)
