#!/usr/bin/env python3
"""포맷 무관 공용 코어 — WikiDocs 전자책 안전 출력의 공통 자산.

여기 모인 것은 입력 포맷(.ipynb / .md / .docx / .pdf …)에 의존하지 않는 함수·상수다.
각 포맷 변환기(어댑터)는 자기 파서로 **raw 마크다운(+추출 이미지)** 을 만든 뒤, 이 코어의
sanitize → 이미지 assets/ 저장 → pages/ 기록 → TOC upsert 를 거쳐 동일한 산출물 규약
(`pages/<이름>.md` · 그림 `assets/<이름>-…` · 목차 `TOC.md`)과 전자책 규칙
(https://wikidocs.net/198723)을 따른다.

설계:
  입력 → [포맷별 변환기]→ raw markdown + 이미지 → [이 코어]→ pages/*.md + assets/ + TOC upsert

노트북(build_wikidocs.py)은 실행 결과 렌더링이라는 고유 단계가 있어 자체 경로를 쓰지만,
sanitize·표 렌더·TOC·헤딩 정리 등 포맷 무관 부분은 모두 이 모듈을 공유한다.
정적 문서(.md/.docx/.pptx/.pdf)는 emit_static_pages() 한 곳으로 흐른다.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

__all__ = [
    # 상수
    "HEADER_RE", "EMOJI_RE", "ANSI_RE", "LEADING_NUM_RE", "H1_CHAPTER_PREFIX_RE",
    "COLAB_BADGE_RE", "SKIP_PATTERNS", "TQDM_BAR_RE",
    "MAX_OUTPUT_LINE_CHARS", "TRUNC_KEEP_CHARS", "PROSE_MIN_TOKENS",
    "MAX_OUTPUT_LINES", "MAX_OUTPUT_CHARS",
    "DEFAULT_LABELS", "DEFAULT_BOOK_TITLE",
    "HR_LINE_RE", "H1_LINE_RE", "HEADING_RE2", "WIN_PATH_RE", "CODE_MATH_RE",
    "RAW_HTML_RE", "EXT_IMG_RE", "INLINE_CODE_RE", "FOOTNOTE_RE", "MD_IMG_RE",
    "OUTPUT_STYLES", "DEFAULT_OUTPUT_STYLE", "OUTPUT_PRE_STYLE", "TOC_LINK_RE",
    # 텍스트/헤딩 유틸
    "_truncate_long_lines", "_clean_heading_text", "_first_header",
    "_strip_colab_badge", "_demote_first_header", "_strip_header_emoji",
    "_wrap_win_paths", "_sanitize_md_cell", "_clean_text_output",
    # 표/출력 렌더
    "_PandasTableParser", "_html_tables_to_text", "_html_escape", "_output_box",
    # TOC
    "_prune_dead_toc_links", "upsert_toc",
    # 정적 문서 공용 코어(어댑터용)
    "new_stats", "make_stem", "markdown_title", "split_markdown_by_h2",
    "localize_images", "emit_static_pages",
]

COLAB_BADGE_RE = re.compile(r"^\s*\[!\[.*?Colab.*?\]\(.*?\)\]\(.*?\)\s*$", re.IGNORECASE)
HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
EMOJI_RE = re.compile(r"^[\s←-⇿⌀-➿⬀-⯿️\U0001F000-\U0001FAFF]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
LEADING_NUM_RE = re.compile(r"^(\d+)[_-](.+)$")  # '07_bert_pipeline' → (7, 'bert_pipeline')
H1_CHAPTER_PREFIX_RE = re.compile(r"^\s*Chapter\s+\d+\s*[.．]\s*")

# 노이즈 필터 — ML 노트북 일반(다운로드·인증·생성 보일러플레이트). 책 내용 아님.
SKIP_PATTERNS = (
    "TqdmWarning:",
    "IProgress not found",
    "Requirement already satisfied:",
    "WARNING: Running pip",
    "[notice] A new release of pip",
    "notice] A new release of pip",
    "To update, run:",
    "huggingface_hub/utils/_auth.py",
    "secret value from your vault",
    "not authenticated with the Hugging Face Hub",
    "If the error persists, please let us know",
    "warnings.warn(",
    "unauthenticated requests to the HF Hub",
    "Setting `pad_token_id`",
    "`max_new_tokens`",
    "clean_up_tokenization_spaces",
    "Passing `generation_config`",
    "aligned accordingly, being updated with the tokenizer",
)

TQDM_BAR_RE = re.compile(r"\d+%\s*\|")

# 긴 산문 줄(리뷰·생성문)은 트렁케이트 — EPUB <pre> 안은 안 접혀 잘림. 표(| 포함)는 보존.
MAX_OUTPUT_LINE_CHARS = 160
TRUNC_KEEP_CHARS = 140
PROSE_MIN_TOKENS = 12
MAX_OUTPUT_LINES = 40
MAX_OUTPUT_CHARS = 2000

DEFAULT_LABELS = {
    "output": "▶ 실행 결과",
    "setup": "환경 준비",
    "roadmap": "이 장의 구성",
}
DEFAULT_BOOK_TITLE = "WikiDocs"


def _truncate_long_lines(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if (len(ln) > MAX_OUTPUT_LINE_CHARS and "|" not in ln
                and len(ln.split()) >= PROSE_MIN_TOKENS):
            omitted = len(ln) - TRUNC_KEEP_CHARS
            ln = ln[:TRUNC_KEEP_CHARS].rstrip() + f" …(뒤 {omitted}자 생략)"
        out.append(ln)
    return out


# --------------------------------------------------------------------------- #
# 텍스트 유틸
# --------------------------------------------------------------------------- #
def _clean_heading_text(text: str) -> str:
    text = re.sub(r"^\s*\d+[.)]\s*", "", text.strip())
    return EMOJI_RE.sub("", text).strip()


def _first_header(md: str) -> tuple[int, str] | None:
    for line in md.splitlines():
        m = HEADER_RE.match(line)
        if m:
            return len(m.group(1)), m.group(2).strip()
    return None


def _strip_colab_badge(md: str) -> str:
    return "\n".join(ln for ln in md.splitlines() if not COLAB_BADGE_RE.match(ln)).strip("\n")


def _demote_first_header(md: str) -> str:
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if HEADER_RE.match(line):
            del lines[i]
            break
    return "\n".join(lines).strip("\n")


def _strip_header_emoji(md: str) -> str:
    out = []
    for line in md.splitlines():
        m = HEADER_RE.match(line)
        if m:
            out.append(f"{m.group(1)} {_clean_heading_text(m.group(2))}")
        else:
            out.append(line)
    return "\n".join(out)


# 전자책 작성 규칙(https://wikidocs.net/198723) 방어용 패턴
HR_LINE_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
H1_LINE_RE = re.compile(r"^#\s+(.*)$")
HEADING_RE2 = re.compile(r"^#{1,6}\s")
WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[\w.\\-]+")
CODE_MATH_RE = re.compile(r"`[^`]*`|\$[^$]+\$")
RAW_HTML_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")
EXT_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
FOOTNOTE_RE = re.compile(r"\[\^([^\]]+)\]")
# 마크다운 이미지 참조: ![alt](target "title") — target 은 <…> 또는 공백 전까지.
MD_IMG_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<target><[^>]+>|[^)\s]+)(?P<rest>[^)]*)\)")


def _wrap_win_paths(ln: str, stats: dict) -> str:
    spans: list[str] = []

    def _stash(m):
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    masked = CODE_MATH_RE.sub(_stash, ln)
    cnt = [0]

    def _wrap(m):
        cnt[0] += 1
        return f"`{m.group(0)}`"

    masked = WIN_PATH_RE.sub(_wrap, masked)
    if not cnt[0]:
        return ln
    stats["win_paths"] += cnt[0]
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], masked)


def _sanitize_md_cell(md: str, stem: str, stats: dict) -> str:
    """마크다운 셀을 전자책 규칙(https://wikidocs.net/198723)에 맞게 방어 정리. 코드펜스 안은 손대지 않음."""
    out: list[str] = []
    fence = False
    pending_blank = False
    for ln in md.split("\n"):
        if pending_blank:
            if ln.strip() != "":
                out.append("")
                stats["heading_blanks"] += 1
            pending_blank = False
        if ln.lstrip().startswith("```"):
            fence = not fence
            out.append(ln)
            continue
        if fence:
            out.append(ln)
            continue
        if HR_LINE_RE.match(ln) and (not out or out[-1].strip() == ""):
            stats["hr_removed"] += 1
            continue
        m = H1_LINE_RE.match(ln)
        if m:
            stats["h1_demoted"] += 1
            ln = "## " + m.group(1)
        if HEADING_RE2.match(ln):
            if out and out[-1].strip() != "":
                out.append("")
                stats["heading_blanks"] += 1
            pending_blank = True
        if ":\\" in ln:
            ln = _wrap_win_paths(ln, stats)
        if "[^" in ln:
            new = FOOTNOTE_RE.sub(lambda x: f"[^{stem}-{x.group(1)}]", ln)
            if new != ln:
                stats["footnotes"] += 1
                ln = new
        scan = INLINE_CODE_RE.sub("", ln)
        stats["html_warn"].extend(RAW_HTML_RE.findall(scan))
        stats["extimg_warn"].extend(EXT_IMG_RE.findall(scan))
        out.append(ln)
    return "\n".join(out)


def _clean_text_output(text: str, truncate: bool = True) -> str:
    text = ANSI_RE.sub("", text)
    lines = [seg.split("\r")[-1] for seg in text.split("\n")]
    lines = [
        ln for ln in lines
        if not any(p in ln for p in SKIP_PATTERNS)
        and not ln.strip().startswith("from .autonotebook import tqdm")
        and not TQDM_BAR_RE.search(ln)
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if truncate:
        lines = _truncate_long_lines(lines)
    if len(lines) > MAX_OUTPUT_LINES:
        lines = lines[: MAX_OUTPUT_LINES - 1] + [
            f"... (출력 {len(lines) - MAX_OUTPUT_LINES + 1}줄 생략) ..."
        ]
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[: MAX_OUTPUT_CHARS - 4].rstrip() + "\n..."
    return text


# --------------------------------------------------------------------------- #
# HTML 표 → 텍스트 표
# --------------------------------------------------------------------------- #
class _PandasTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict[str, list]] = []
        self.in_table = self.in_row = self.in_cell = False
        self.cell_is_header = False
        self.current_cell: list[str] = []
        self.current_row: list[tuple[bool, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.tables.append({"headers": [], "rows": []})
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_table and self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_is_header = tag == "th"
            self.current_cell = []
        elif self.in_cell and tag == "br":
            self.current_cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.in_cell:
            text = unescape("".join(self.current_cell))
            text = re.sub(r"\s+", " ", text).strip()
            self.current_row.append((self.cell_is_header, text))
            self.in_cell = False
            self.current_cell = []
        elif tag == "tr" and self.in_row:
            if self.current_row and self.tables:
                values = [v for _, v in self.current_row]
                header_count = sum(1 for is_h, _ in self.current_row if is_h)
                data_count = len(self.current_row) - header_count
                table = self.tables[-1]
                if header_count >= data_count:
                    table["headers"] = values
                else:
                    table["rows"].append(values)
            self.in_row = False
            self.current_row = []
        elif tag == "table":
            self.in_table = False

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def _html_tables_to_text(html: str) -> list[str]:
    parser = _PandasTableParser()
    parser.feed(html)
    out: list[str] = []
    for table in parser.tables:
        headers, rows = table["headers"], table["rows"]
        if not rows:
            continue
        width = max([len(headers)] + [len(r) for r in rows])
        headers = (headers + [""] * width)[:width] if headers else [""] * width
        shown = [(r + [""] * width)[:width] for r in rows[:30]]
        grid = [headers] + shown
        colw = [max(len(str(row[c])) for row in grid) for c in range(width)]
        def fmt(row): return "  ".join(str(row[c]).ljust(colw[c]) for c in range(width)).rstrip()
        lines = [fmt(headers)] + [fmt(r) for r in shown]
        if len(rows) > 30:
            lines.append("...")
        out.append("\n".join(lines))
    return out


# --------------------------------------------------------------------------- #
# 출력 박스
# --------------------------------------------------------------------------- #
# 출력 박스 표현: code(기본, 웹·PDF·EPUB 모두 안전) — 실측상 세 타깃 모두 만족하는 건 code 뿐.
OUTPUT_STYLES = ("code", "html-box")
DEFAULT_OUTPUT_STYLE = "code"
OUTPUT_PRE_STYLE = (
    "background:#eef3fb;border-left:4px solid #5B8DEF;"
    "padding:0.7em 1em;border-radius:4px;overflow-x:auto;"
    "font-size:0.92em;line-height:1.45;"
)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _output_box(text: str, style: str) -> str:
    if style == "html-box":
        return f'<pre style="{OUTPUT_PRE_STYLE}">{_html_escape(text)}</pre>'
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}"


# --------------------------------------------------------------------------- #
# TOC
# --------------------------------------------------------------------------- #
TOC_LINK_RE = re.compile(r"^\s*[*-]\s*\[[^\]]*\]\(([^)]+)\)\s*$")


def _prune_dead_toc_links(lines: list[str], toc_dir: Path) -> tuple[list[str], list[str]]:
    """TOC 리스트 항목 중 '없는 로컬 .md 페이지' 를 가리키는 줄을 제거한다.

    외부 URL(`://`)·앵커(`#…`)·`.md` 가 아닌 링크는 그대로 둔다. (kept_lines, removed_targets) 반환.
    """
    kept: list[str] = []
    removed: list[str] = []
    for ln in lines:
        m = TOC_LINK_RE.match(ln)
        if m:
            target = m.group(1).strip()
            path_part = target.split("#", 1)[0]
            is_local_md = (
                path_part.endswith(".md")
                and "://" not in target
                and not target.startswith("#")
            )
            if is_local_md and not (toc_dir / path_part).exists():
                removed.append(path_part)
                continue
        kept.append(ln)
    return kept, removed


def upsert_toc(toc_path: Path, book_title: str, num: int | None, name: str,
               entries: list[tuple[str, str]]) -> None:
    """TOC.md 에서 이 항목 블록만 교체/추가. 번호가 있으면 NN. / NN-N. 블록을 키로 쓴다.

    더불어 없는 로컬 `.md` 페이지를 가리키는 죽은 링크 줄은 정리한다.
    """
    new_lines = []
    for title, path in entries:
        indent = "" if (num is None or re.match(r"^\d+\.\s", title)) else "  "
        new_lines.append(f"{indent}* [{title}]({path})")

    if not toc_path.exists():
        toc_path.write_text(f"# {book_title}\n\n" + "\n".join(new_lines) + "\n", encoding="utf-8")
        return

    lines = toc_path.read_text(encoding="utf-8").splitlines()

    if num is None:
        # 번호 없는 항목: 같은 첫 링크 경로가 이미 있으면 그 블록 교체, 없으면 끝에 추가.
        first_path = entries[0][1]
        anchor = re.compile(rf"^\s*\*\s*\[[^\]]*\]\({re.escape(first_path)}\)")
        idx = next((i for i, ln in enumerate(lines) if anchor.match(ln)), None)
        if idx is None:
            out = lines + new_lines
        else:
            # 이 블록(들여쓰기 절 포함)을 한 덩어리로 보고 교체: 다음 최상위 항목 전까지.
            end = idx + 1
            while end < len(lines) and (lines[end].startswith("  ") or lines[end].strip() == ""):
                end += 1
            out = lines[:idx] + new_lines + lines[end:]
        out, removed = _prune_dead_toc_links(out, toc_path.parent)
        if removed:
            print(f"     TOC 정리: 없는 페이지 링크 {len(removed)}건 제거 ({', '.join(removed)})")
        toc_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        return

    nn = f"{num:02d}"
    chapter_re = re.compile(rf"^\s*\*\s*\[{nn}[.\-]")
    start = end = None
    for i, ln in enumerate(lines):
        if chapter_re.match(ln):
            if start is None:
                start = i
            end = i
    if start is None:
        insert_at = len(lines)
        any_chapter = re.compile(r"^\s*\*\s*\[(\d{2})[.\-]")
        for i, ln in enumerate(lines):
            m = any_chapter.match(ln)
            if m and int(m.group(1)) > num:
                insert_at = i
                break
        out = lines[:insert_at] + new_lines + lines[insert_at:]
    else:
        out = lines[:start] + new_lines + lines[end + 1:]
    out, removed = _prune_dead_toc_links(out, toc_path.parent)
    if removed:
        print(f"     TOC 정리: 없는 페이지 링크 {len(removed)}건 제거 ({', '.join(removed)})")
    toc_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 정적 문서 공용 코어 (어댑터용) — .md/.docx/.pptx/.pdf 변환기가 함께 쓴다.
# --------------------------------------------------------------------------- #
# sanitize 가 채우는 통계/경고 누적기. 포맷 변환기는 이걸 만들어 코어에 넘긴다.
def new_stats() -> dict:
    return {
        "hr_removed": 0, "h1_demoted": 0, "footnotes": 0,
        "heading_blanks": 0, "win_paths": 0,
        "images": 0, "sections": 0,
        "html_warn": [], "extimg_warn": [],
    }


def make_stem(name: str, num: int | None) -> str:
    """페이지 파일 베이스: 'NN_slug' → 'NN-slug', 그 외엔 이름 그대로(노트북 경로와 동일 규약)."""
    m = LEADING_NUM_RE.match(name)
    if num is not None and m:
        return f"{num:02d}-{m.group(2)}"
    return name


def markdown_title(md: str, fallback: str) -> str:
    """문서 제목: 첫 H1(없으면 첫 헤딩) 텍스트, 없으면 fallback(보통 슬러그)."""
    hdr = _first_header(md)
    if hdr:
        return H1_CHAPTER_PREFIX_RE.sub("", _clean_heading_text(hdr[1])).strip() or fallback
    return fallback


def split_markdown_by_h2(md: str) -> tuple[str, list[tuple[str, str]]]:
    """마크다운을 H2(`## …`) 헤딩 단위로 분할. 코드펜스 안의 `##` 은 무시한다.

    반환: (첫 H2 이전 도입부, [(절 제목, 절 본문(헤딩 줄 포함)), …]). 절 본문은 다음 H2 직전까지.
    """
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur: list[str] | None = None
    fence = False
    for ln in md.split("\n"):
        if ln.lstrip().startswith("```"):
            fence = not fence
            (cur if cur is not None else intro).append(ln)
            continue
        m = HEADER_RE.match(ln) if not fence else None
        if m and len(m.group(1)) == 2:
            cur = [ln]
            sections.append((m.group(2).strip(), cur))
        else:
            (cur if cur is not None else intro).append(ln)
    return ("\n".join(intro).strip("\n"),
            [(t, "\n".join(body).strip("\n")) for t, body in sections])


def _guess_img_ext(target: str, content_type: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(target).path).suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        return suffix
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ".jpg" if ext == ".jpe" else ext
    return ".png"


def localize_images(md: str, assets_dir: Path | None, stem: str, base_dir: Path,
                    stats: dict, counter: list[int] | None = None) -> str:
    """본문의 외부/로컬 이미지를 assets/ 로 내려받거나 복사하고 상대경로(`../assets/…`)로 치환.

    전자책(특히 PDF/EPUB)은 외부 URL 이미지가 누락되므로 로컬 자산화가 필수다. 코드펜스 안은 손대지
    않으며, 이미 `../assets/` 를 가리키는 참조나 data: URI 는 그대로 둔다. 다운로드/복사에 실패한
    참조는 원본 그대로 두고(이후 sanitize 의 외부 이미지 경고로 드러남) 진행한다.
    """
    if assets_dir is None:
        return md
    if counter is None:
        counter = [0]

    def _save(target: str) -> str | None:
        is_http = target.startswith(("http://", "https://"))
        try:
            if is_http:
                req = urllib.request.Request(target, headers={"User-Agent": "wikidocs-convert"})
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (문서화된 동작)
                    data = resp.read()
                    ext = _guess_img_ext(target, resp.headers.get("Content-Type"))
            else:
                src = Path(target)
                src = src if src.is_absolute() else (base_dir / target)
                if not src.exists():
                    return None
                data = src.read_bytes()
                ext = src.suffix.lower() or ".png"
            counter[0] += 1
            assets_dir.mkdir(parents=True, exist_ok=True)
            img_name = f"{stem}-img{counter[0]}{ext}"
            (assets_dir / img_name).write_bytes(data)
            stats["images"] += 1
            return img_name
        except Exception as e:  # noqa: BLE001 (변환은 멈추지 않는다 — 원본 참조 유지)
            print(f"     ⚠ 이미지 가져오기 실패({target}): {e}")
            return None

    def _repl(m: re.Match) -> str:
        target = m.group("target").strip().strip("<>")
        if not target or target.startswith("data:"):
            return m.group(0)
        norm = target.replace("\\", "/")
        if norm.startswith("../assets/") or norm.startswith("assets/"):
            return m.group(0)
        img_name = _save(target)
        if img_name is None:
            return m.group(0)
        return f"![{m.group('alt')}](../assets/{img_name}{m.group('rest')})"

    out: list[str] = []
    fence = False
    for ln in md.split("\n"):
        if ln.lstrip().startswith("```"):
            fence = not fence
            out.append(ln)
            continue
        out.append(ln if fence else MD_IMG_RE.sub(_repl, ln))
    return "\n".join(out)


def _strip_leading_h1(md: str) -> str:
    """문서의 첫 H1(문서 제목) 한 줄을 제거한다 — 페이지 제목은 TOC 가 담당(중복 방지).

    첫 헤딩이 H1 일 때만 그 줄을 지운다(H2 로 시작하면 그대로 둬 절로 쓰인다). 코드펜스 안은 무시.
    """
    lines = md.split("\n")
    fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = HEADER_RE.match(ln)
        if m:
            if len(m.group(1)) == 1:
                del lines[i]
            break
    return "\n".join(lines).strip("\n")


def emit_static_pages(raw_md: str, *, stem: str, num: int | None, title: str, name: str,
                      pages_dir: Path, toc_path: Path, book_title: str,
                      split: bool, stats: dict, labels: dict | None = None) -> list[tuple[str, str]]:
    """정적 문서의 raw 마크다운 → pages/*.md + TOC upsert (노트북 산출물 규약과 동일 형태).

    split=False: 문서 1개 = 페이지 1개. split=True: H2 단위로 개요(+로드맵) + 절 서브페이지.
    문서의 첫 H1(제목)은 제거되고 TOC 에 들어간다. 이미지는 호출 전에 localize_images() 로
    이미 `../assets/` 로 치환돼 있다고 가정한다. 반환: TOC entries [(제목, 'pages/…md')].
    """
    labels = labels or DEFAULT_LABELS
    pages_dir.mkdir(parents=True, exist_ok=True)
    num_prefix = f"{num:02d}. " if num is not None else ""
    raw_md = _strip_leading_h1(raw_md)

    if not split:
        body = _sanitize_md_cell(raw_md, stem, stats).strip()
        (pages_dir / f"{stem}.md").write_text(body + "\n", encoding="utf-8")
        stats["sections"] = 0
        entries = [(f"{num_prefix}{title}", f"pages/{stem}.md")]
        upsert_toc(toc_path, book_title, num, name, entries)
        return entries

    # 원본 기준으로 먼저 H2 분할(분할 후 각 조각을 sanitize). 도입부의 H1 은 sanitize 가 H2 로 강등.
    intro_md, secs = split_markdown_by_h2(raw_md)
    ov_parts: list[str] = []
    intro_clean = _sanitize_md_cell(intro_md, stem, stats).strip()
    if intro_clean:
        ov_parts.append(intro_clean)
    if secs:
        roadmap = [f"## {labels['roadmap']}", ""]
        for idx, (stitle, _) in enumerate(secs, 1):
            prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
            roadmap.append(f"- [{prefix}{_clean_heading_text(stitle)}]({stem}-{idx}.md)")
        ov_parts.append("\n".join(roadmap))
    (pages_dir / f"{stem}.md").write_text("\n\n".join(ov_parts).strip() + "\n", encoding="utf-8")
    entries: list[tuple[str, str]] = [(f"{num_prefix}{title}", f"pages/{stem}.md")]

    for idx, (stitle, body) in enumerate(secs, 1):
        clean = _strip_header_emoji(_sanitize_md_cell(body, stem, stats))
        clean = _demote_first_header(clean)   # 절 제목은 TOC 가 담당 → 본문 첫 헤딩 제거(중복 방지)
        (pages_dir / f"{stem}-{idx}.md").write_text(clean.strip() + "\n", encoding="utf-8")
        prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
        entries.append((f"{prefix}{_clean_heading_text(stitle)}", f"pages/{stem}-{idx}.md"))

    stats["sections"] = len(secs)
    upsert_toc(toc_path, book_title, num, name, entries)
    return entries
