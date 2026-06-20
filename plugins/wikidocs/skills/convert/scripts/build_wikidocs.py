#!/usr/bin/env python3
"""Jupyter 노트북(.ipynb)을 WikiDocs 연동용 마크다운으로 변환한다.

핵심 차이는 단순 파싱이 아니라 **코드의 실제 실행 결과(표·로그·그림)를 함께 싣는다**는 점이다.
노트북은 보통 출력이 비어 있으므로(배포용 clean 상태), 다음 우선순위로 "실제 결과"를 확보한다.

출력 원천 우선순위 (노트북별 자동):
  1) --executed-notebook PATH    : (단일 노트북) 미리 실행해 outputs를 담은 노트북
  2) <stem>_executed.ipynb       : 소스 노트북 옆에 같은 이름+_executed 가 있으면 자동 사용
                                   (실행 결과 없는 노트북을 한 번 실행해 붙여둔 결과물)
  3) 노트북 자체의 outputs        : 입력 노트북에 이미 출력이 박혀 있으면 그대로 사용
  4) --execute                   : 이 자리에서 직접 실행(표준 라이브러리만, 주로 CPU 노트북).
                                   --save-executed 면 결과를 <stem>_executed.ipynb 로 저장.
  5) (없음)                      : 코드만 싣는다(가짜 출력 금지). 노트북 전체에 출력이 없으면
                                   원천=출력없음 으로 표시되어 ①에서 사용자에게 실행 여부를 묻는다.

GPU 실행이 필요하면 colab-cli 러너(run_via_cli.sh)가 Colab 에서 돌려 <stem>_executed.ipynb 를
만든다. executed/ 같은 별도 보관 폴더는 쓰지 않는다 — 실행본은 소스 옆에 _executed 로 둔다.

분할(장→절):
  --split single (기본)  : 노트북 1개 = 페이지 1개. 의존성·설정 없이 동작.
  --split sections       : 한 장을 여러 절로 분할.
                           · config 없음 → H2(## …) 헤딩 단위 구조적 분할(키워드 불필요).
                           · --config 의 section_rules → 키워드로 고정 버킷 분할(레거시).

노트북 지정:
  - 위치 인자로 .ipynb 경로를 직접 주거나, 번호/이름(--root 아래에서 발견)으로 줄 수 있다.
  - --all 이면 --root 아래의 .ipynb 를 자동 발견(<이름>_executed·체크포인트·러너는 제외).

사용:
  python3 build_wikidocs.py path/to/notebook.ipynb
  python3 build_wikidocs.py report.ipynb --split sections          # H2 헤딩 단위 분할
  python3 build_wikidocs.py --all --root ~/proj
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import traceback
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

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


def _truncate_long_lines(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if (len(ln) > MAX_OUTPUT_LINE_CHARS and "|" not in ln
                and len(ln.split()) >= PROSE_MIN_TOKENS):
            omitted = len(ln) - TRUNC_KEEP_CHARS
            ln = ln[:TRUNC_KEEP_CHARS].rstrip() + f" …(뒤 {omitted}자 생략)"
        out.append(ln)
    return out


# 기본 분할 규칙(--split sections 이면서 config 미지정 시의 폴백) — 한글 커리큘럼 키워드.
# 일반 프로젝트는 --config 로 자기 키워드를 주거나, 기본값(single)을 쓴다.
DEFAULT_SECTION_RULES: list[tuple[str, str]] = [
    ("삽질", "wrapup"), ("라이브러리", "wrapup"), ("체크포인트", "wrapup"),
    ("FAQ", "wrapup"), ("다음 챕터", "wrapup"), ("다음 장", "wrapup"), ("예고", "wrapup"),
    ("실습", "practice"), ("해부", "anatomy"), ("변형", "variation"),
]
DEFAULT_SUBPAGES: list[tuple[str, str, str]] = [
    ("practice", "practice", "실습"), ("anatomy", "anatomy", "해부"),
    ("variation", "variation", "변형"), ("wrapup", "wrapup", "정리와 FAQ"),
]

DEFAULT_LABELS = {
    "output": "▶ 실행 결과",
    "setup": "환경 준비",
    "roadmap": "이 장의 구성",
}
DEFAULT_BOOK_TITLE = "WikiDocs"


# --------------------------------------------------------------------------- #
# 텍스트 유틸
# --------------------------------------------------------------------------- #
def _cell_text(cell: dict) -> str:
    src = cell.get("source", "")
    return src if isinstance(src, str) else "".join(src)


def _clean_heading_text(text: str) -> str:
    text = re.sub(r"^\s*\d+[.)]\s*", "", text.strip())
    return EMOJI_RE.sub("", text).strip()


def _first_header(md: str) -> tuple[int, str] | None:
    for line in md.splitlines():
        m = HEADER_RE.match(line)
        if m:
            return len(m.group(1)), m.group(2).strip()
    return None


def _classify(header_text: str, section_rules: list[tuple[str, str]]) -> str:
    for kw, group in section_rules:
        if kw in header_text:
            return group
    return "overview"


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
# 셀 출력 렌더링
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


def _render_outputs(cell: dict, assets_dir: Path | None, stem: str, counter: list[int],
                    label: str, style: str = DEFAULT_OUTPUT_STYLE, truncate: bool = True) -> str:
    items: list[tuple[str, str]] = []
    for out in cell.get("outputs", []):
        otype = out.get("output_type")
        if otype == "stream":
            text = _clean_text_output("".join(out.get("text", [])), truncate)
            if text.strip():
                items.append(("text", text))
        elif otype in ("execute_result", "display_data"):
            data = out.get("data", {})
            if "image/png" in data:
                counter[0] += 1
                img_name = f"{stem}-out{counter[0]}.png"
                if assets_dir is not None:
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    raw = data["image/png"]
                    raw = raw if isinstance(raw, str) else "".join(raw)
                    (assets_dir / img_name).write_bytes(base64.b64decode(raw))
                items.append(("image", img_name))
                continue
            text = data.get("text/plain")
            if text:
                text = _clean_text_output("".join(text) if isinstance(text, list) else str(text), truncate)
                if text.strip():
                    items.append(("text", text))
                continue
            html = data.get("text/html")
            if isinstance(html, list):
                html = "".join(html)
            if isinstance(html, str) and "<table" in html:
                for t in _html_tables_to_text(html):
                    items.append(("text", t))
        elif otype == "error":
            tb = out.get("traceback", [])
            if tb:
                text = _clean_text_output("\n".join(str(l) for l in tb[-8:]), truncate)
            else:
                text = f"{out.get('ename', 'Error')}: {out.get('evalue', '')}"
            if text.strip():
                items.append(("text", text))

    if not items:
        return ""

    blocks: list[str] = [f"**{label}**"]
    buf: list[str] = []

    def flush_text():
        if buf:
            blocks.append(_output_box("\n".join(buf), style))
            buf.clear()

    for kind, val in items:
        if kind == "text":
            buf.append(val)
        else:
            flush_text()
            blocks.append(f"![output](../assets/{val})")
    flush_text()
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# 노트북 실행 (선택) — 표준 라이브러리만 사용 (nbclient·nbformat·jupyter 불필요)
# --------------------------------------------------------------------------- #
# 코드 셀들을 한 파이썬 서브프로세스에서 순서대로 실행해 출력을 캡처하는 드라이버.
# stdout/stderr(stream) · 셀 마지막 표현식 repr(execute_result, _repr_html_ 있으면 표까지) ·
# matplotlib 그림(display_data PNG) · 예외(error)를 nbformat JSON 모양으로 모은다.
# 위젯 등 리치 출력은 지원하지 않는다 — CPU 노트북용 경량 실행기.
# (노트북 자체의 의존성(sklearn 등)은 같은 인터프리터에 설치돼 있어야 하는 건 nbclient 와 동일.)
_EXEC_DRIVER = r'''
import sys, json, io, ast, base64, traceback
from contextlib import redirect_stdout, redirect_stderr

cells = json.load(open(sys.argv[1], encoding="utf-8"))
results = []
g = {"__name__": "__main__"}
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
except Exception:
    _plt = None

for src in cells:
    outs = []
    buf = io.StringIO()
    val = None
    err = None
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            mod = ast.parse(src)
            last = mod.body[-1] if mod.body else None
            if isinstance(last, ast.Expr):
                if mod.body[:-1]:
                    exec(compile(ast.Module(mod.body[:-1], []), "<cell>", "exec"), g)
                val = eval(compile(ast.Expression(last.value), "<cell>", "eval"), g)
            else:
                exec(compile(mod, "<cell>", "exec"), g)
        except Exception:
            et, ev, tb = sys.exc_info()
            err = "".join(traceback.format_exception(et, ev, tb.tb_next if tb else tb))
    text = buf.getvalue()
    if text:
        outs.append({"output_type": "stream", "name": "stdout", "text": text})
    if _plt is not None:
        for num in _plt.get_fignums():
            fig = _plt.figure(num)
            b = io.BytesIO()
            try:
                fig.savefig(b, format="png", bbox_inches="tight")
                outs.append({"output_type": "display_data",
                             "data": {"image/png": base64.b64encode(b.getvalue()).decode("ascii")},
                             "metadata": {}})
            except Exception:
                pass
        _plt.close("all")
    if err is not None:
        line = err.strip().splitlines()[-1]
        outs.append({"output_type": "error", "ename": line.split(":")[0],
                     "evalue": line, "traceback": err.splitlines()})
    elif val is not None:
        data = {"text/plain": repr(val)}
        html = getattr(val, "_repr_html_", None)
        if callable(html):
            try:
                h = html()
                if isinstance(h, str):
                    data["text/html"] = h
            except Exception:
                pass
        outs.append({"output_type": "execute_result", "data": data,
                     "metadata": {}, "execution_count": None})
    results.append(outs)

json.dump(results, open(sys.argv[2], "w", encoding="utf-8"))
'''


def execute_notebook(path: Path, timeout: int = 1800) -> dict:
    """표준 라이브러리만으로 노트북 코드 셀을 실행해 outputs 를 채운 dict 를 돌려준다."""
    import subprocess
    import tempfile

    nb = json.loads(Path(path).read_text(encoding="utf-8"))
    code_cells = [c for c in nb.get("cells", []) if c.get("cell_type") == "code"]
    sources = [_cell_text(c) for c in code_cells]

    with tempfile.TemporaryDirectory() as td:
        cells_in = Path(td) / "cells.json"
        outs_out = Path(td) / "outs.json"
        driver = Path(td) / "driver.py"
        cells_in.write_text(json.dumps(sources), encoding="utf-8")
        driver.write_text(_EXEC_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(driver), str(cells_in), str(outs_out)],
            cwd=str(Path(path).parent), timeout=timeout,
            capture_output=True, text=True,
        )
        if not outs_out.exists():
            raise RuntimeError(f"노트북 실행기 실패: {proc.stderr.strip()[-300:] or proc.stdout.strip()[-300:]}")
        all_outs = json.loads(outs_out.read_text(encoding="utf-8"))

    for cell, outs in zip(code_cells, all_outs):
        cell["outputs"] = outs
        cell["execution_count"] = None
    return nb


def chapter_h1_title(nb: dict) -> str:
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        m = _first_header(_cell_text(cell))
        if m and m[0] == 1:
            return H1_CHAPTER_PREFIX_RE.sub("", m[1]).strip()
    return ""


# --------------------------------------------------------------------------- #
# 변환
# --------------------------------------------------------------------------- #
def _convert_structural(nb: dict, num: int | None, title: str, stem: str,
                        pages_dir: Path, assets_dir: Path | None, labels: dict,
                        style: str, truncate: bool, img_counter: list,
                        stats: dict) -> tuple[list[tuple[str, str]], dict]:
    """config 없이 H2(`## …`) 헤딩 단위로 장→절 분할(키워드·매핑 불필요).

    H1/도입부와 첫 H2 이전 내용은 개요 페이지(+자동 로드맵)로, 각 H2 는 문서 순서대로
    제 슬러그(`{stem}-1`, `-2`, …)를 가진 서브페이지가 된다. 절 제목은 헤딩 텍스트 그대로.
    """
    sections: list[dict] = []          # [{title, blocks}], 문서 순서
    overview_intro: list[str] = []     # H1 본문
    overview_pre: list[str] = []       # 첫 H2 이전 마크다운
    setup_code: list[str] = []         # 첫 H2 이전 코드
    cur: dict | None = None            # 현재 절(None 이면 아직 개요)
    seen_h1 = False

    for cell in nb.get("cells", []):
        ctype = cell.get("cell_type")
        if ctype == "markdown":
            md = _strip_colab_badge(_cell_text(cell))
            if not md.strip():
                continue
            hdr = _first_header(md)
            if hdr and hdr[0] == 1 and not seen_h1:
                seen_h1 = True
                body = "\n".join(md.splitlines()[1:]).strip("\n")
                if body.strip():
                    overview_intro.append(_sanitize_md_cell(body, stem, stats))
                continue
            if hdr and hdr[0] == 2:
                cur = {"title": _clean_heading_text(hdr[1]), "blocks": []}
                sections.append(cur)
            block = _strip_header_emoji(_sanitize_md_cell(md, stem, stats))
            (cur["blocks"] if cur else overview_pre).append(block)
        elif ctype == "code":
            code = _cell_text(cell).rstrip("\n")
            if not code.strip():
                continue
            stats["code_cells"] += 1
            outs = _render_outputs(cell, assets_dir, stem, img_counter,
                                   labels["output"], style, truncate)
            if outs:
                stats["code_with_output"] += 1
            else:
                stats["no_output"] += 1
            piece = "```python\n" + code + "\n```" + ("\n\n" + outs if outs else "")
            (cur["blocks"] if cur else setup_code).append(piece)

    stats["images"] = img_counter[0]
    stats["sections"] = len(sections)
    pages_dir.mkdir(parents=True, exist_ok=True)
    num_prefix = f"{num:02d}. " if num is not None else ""
    toc_entries: list[tuple[str, str]] = []

    # 개요 페이지: 도입부 + (첫 H2 이전 코드는 '환경 준비') + 로드맵.
    ov: list[str] = list(overview_intro) + list(overview_pre)
    if setup_code:
        ov.append(f"## {labels['setup']}\n\n" + "\n\n".join(setup_code))
    if sections:
        roadmap = [f"## {labels['roadmap']}", ""]
        for idx, sec in enumerate(sections, 1):
            prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
            roadmap.append(f"- [{prefix}{sec['title']}]({stem}-{idx}.md)")
        ov.append("\n".join(roadmap))
    (pages_dir / f"{stem}.md").write_text(
        "\n\n".join(b for b in ov if b).strip() + "\n", encoding="utf-8")
    toc_entries.append((f"{num_prefix}{title}", f"pages/{stem}.md"))

    # 절 페이지: 페이지 제목이 헤딩을 대신하므로 본문 첫 헤딩은 제거(중복 방지).
    for idx, sec in enumerate(sections, 1):
        blocks = list(sec["blocks"])
        if blocks:
            blocks[0] = _demote_first_header(blocks[0])
        (pages_dir / f"{stem}-{idx}.md").write_text(
            "\n\n".join(b for b in blocks if b).strip() + "\n", encoding="utf-8")
        prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
        toc_entries.append((f"{prefix}{sec['title']}", f"pages/{stem}-{idx}.md"))

    return toc_entries, stats


def convert(nb: dict, num: int | None, name: str, slug: str, title: str,
            pages_dir: Path, assets_dir: Path | None, cfg: dict,
            style: str = DEFAULT_OUTPUT_STYLE) -> tuple[list[tuple[str, str]], dict]:
    """노트북 dict → pages/*.md. cfg 로 분할 모드·라벨·트렁케이트 제외를 제어."""
    split = cfg["split"]
    section_rules = cfg["section_rules"]
    subpages = cfg["subpages"]
    labels = cfg["labels"]
    no_truncate = set(cfg["no_truncate"])

    # 페이지 파일 베이스: 'NN_slug' → 'NN-slug', 그 외엔 이름 그대로. (_executed 접미사는 제거)
    base = base_name(name)
    if num is not None and LEADING_NUM_RE.match(base):
        stem = f"{num:02d}-{LEADING_NUM_RE.match(base).group(2)}"
    else:
        stem = base
    truncate = name not in no_truncate and stem not in no_truncate
    img_counter = [0]
    stats = {"code_cells": 0, "code_with_output": 0, "no_output": 0, "images": 0,
             "hr_removed": 0, "h1_demoted": 0, "footnotes": 0, "heading_blanks": 0, "win_paths": 0,
             "sections": 0, "html_warn": [], "extimg_warn": []}

    # config 없이 sections 면 H2 헤딩 단위 구조적 분할(키워드 매핑 불필요).
    if split == "sections" and cfg.get("section_mode") == "structural":
        return _convert_structural(nb, num, title, stem, pages_dir, assets_dir,
                                   labels, style, truncate, img_counter, stats)

    groups: dict[str, list[str]] = {
        "overview": [], "practice": [], "anatomy": [], "variation": [], "wrapup": []
    }
    sub_titles: dict[str, str] = {}
    overview_intro: list[str] = []
    setup_code: list[str] = []

    current = "overview"
    seen_h1 = False

    for cell in nb.get("cells", []):
        ctype = cell.get("cell_type")
        if ctype == "markdown":
            md = _strip_colab_badge(_cell_text(cell))
            if not md.strip():
                continue
            hdr = _first_header(md)
            if hdr and hdr[0] == 1 and not seen_h1:
                seen_h1 = True
                body = "\n".join(md.splitlines()[1:]).strip("\n")
                if body.strip():
                    overview_intro.append(_sanitize_md_cell(body, stem, stats))
                continue
            if split == "sections" and hdr and hdr[0] == 2:
                current = _classify(hdr[1], section_rules)
                if current in ("practice", "anatomy", "variation"):
                    sub_titles[current] = _clean_heading_text(hdr[1])
            groups[current].append(_strip_header_emoji(_sanitize_md_cell(md, stem, stats)))
        elif ctype == "code":
            code = _cell_text(cell).rstrip("\n")
            if not code.strip():
                continue
            stats["code_cells"] += 1
            block = "```python\n" + code + "\n```"
            outs = _render_outputs(cell, assets_dir, stem, img_counter, labels["output"], style, truncate)
            if outs:
                stats["code_with_output"] += 1
            else:
                # 실제 출력이 없으면 코드만 싣는다(가짜 출력 금지). 노트북 전체에 출력이 없으면
                # 호출부(원천=출력없음)가 드러내고 ①에서 사용자에게 실행 여부를 묻는다.
                stats["no_output"] += 1
            piece = block + ("\n\n" + outs if outs else "")
            if current == "overview" and split == "sections":
                setup_code.append(piece)
            else:
                groups[current].append(piece)

    stats["images"] = img_counter[0]
    pages_dir.mkdir(parents=True, exist_ok=True)
    toc_entries: list[tuple[str, str]] = []
    num_prefix = f"{num:02d}. " if num is not None else ""

    # single 모드: 모든 내용을 한 페이지로.
    if split == "single":
        body = overview_intro + groups["overview"]
        (pages_dir / f"{stem}.md").write_text("\n\n".join(b for b in body if b).strip() + "\n",
                                              encoding="utf-8")
        toc_entries.append((f"{num_prefix}{title}", f"pages/{stem}.md"))
        return toc_entries, stats

    # sections 모드: 개요 + 절들.
    ov: list[str] = []
    ov.extend(overview_intro)
    ov.extend(groups["overview"])
    present_subs = [(g, sl, sub_titles.get(g, dt)) for g, sl, dt in subpages
                    if groups[g] or (g == "practice" and setup_code)]
    stats["sections"] = len(present_subs)
    roadmap = [f"## {labels['roadmap']}", ""]
    for idx, (g, sl, t) in enumerate(present_subs, 1):
        prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
        roadmap.append(f"- [{prefix}{t}]({stem}-{sl}.md)")
    ov.append("\n".join(roadmap))
    (pages_dir / f"{stem}.md").write_text("\n\n".join(ov).strip() + "\n", encoding="utf-8")
    toc_entries.append((f"{num_prefix}{title}", f"pages/{stem}.md"))

    for idx, (g, sl, dt) in enumerate(present_subs, 1):
        parts: list[str] = []
        body_blocks = list(groups[g])
        if g == "practice" and setup_code:
            parts.append(f"## {labels['setup']}\n\n" + "\n\n".join(setup_code))
        if g in ("practice", "anatomy", "variation") and body_blocks:
            body_blocks[0] = _demote_first_header(body_blocks[0])
        parts.extend(body_blocks)
        (pages_dir / f"{stem}-{sl}.md").write_text(
            "\n\n".join(p for p in parts if p).strip() + "\n", encoding="utf-8")
        t = sub_titles.get(g, dt)
        prefix = f"{num:02d}-{idx}. " if num is not None else f"{idx}. "
        toc_entries.append((f"{prefix}{t}", f"pages/{stem}-{sl}.md"))

    return toc_entries, stats


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
# 노트북 발견 / 선택
# --------------------------------------------------------------------------- #
RUNNER_NAMES = {"run_on_colab", "run_via_cli", "colab_cli_exec"}
EXECUTED_SUFFIX = "_executed"  # 실행본 명명 규약: <소스>_executed.ipynb


def discover_notebooks(root: Path) -> dict[str, tuple[int | None, str, Path]]:
    """{name: (num, slug, nb_path)} — root 아래 .ipynb 자동 발견.

    1) 폴더 규약: NN_slug/NN_slug.ipynb (폴더와 노트북 이름이 같을 때)
    2) 평평한 .ipynb (루트 직속, 러너·체크포인트 제외)
    실행본(<이름>_executed.ipynb)은 소스가 아니므로 발견 대상에서 제외한다.
    """
    found: dict[str, tuple[int | None, str, Path]] = {}

    def add(nb: Path):
        name = nb.stem
        if name in RUNNER_NAMES or name.endswith(EXECUTED_SUFFIX):
            return
        m = LEADING_NUM_RE.match(name)
        num = int(m.group(1)) if m else None
        slug = m.group(2) if m else name
        found.setdefault(name, (num, slug, nb))

    # 폴더 규약 우선
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in ("assets", "pages") or d.name.startswith("."):
            continue
        nb = d / f"{d.name}.ipynb"
        if nb.exists():
            add(nb)
    # 루트 직속 .ipynb
    for nb in sorted(root.glob("*.ipynb")):
        if ".ipynb_checkpoints" in str(nb):
            continue
        add(nb)
    return found


def resolve_title(num: int | None, slug: str, nb: dict) -> str:
    h1 = chapter_h1_title(nb)
    if h1:
        return h1
    return slug.replace("_", " ")


def parse_chapter_args(tokens: list[str], available: dict[str, tuple], root: Path) -> list[str]:
    """'07_bert_pipeline' / '7' / '07' / 'path/to/nb.ipynb' → available 의 키(name) 리스트."""
    keys: list[str] = []
    by_num = {v[0]: k for k, v in available.items() if v[0] is not None}
    for tok in tokens:
        # 직접 경로
        p = Path(tok)
        if tok.endswith(".ipynb"):
            pp = p if p.is_absolute() else root / p
            if not pp.exists():
                raise SystemExit(f"노트북을 찾을 수 없습니다: {tok}")
            name = pp.stem
            available.setdefault(name, (
                int(LEADING_NUM_RE.match(name).group(1)) if LEADING_NUM_RE.match(name) else None,
                LEADING_NUM_RE.match(name).group(2) if LEADING_NUM_RE.match(name) else name,
                pp))
            if name not in keys:
                keys.append(name)
            continue
        if tok in available:
            if tok not in keys:
                keys.append(tok)
            continue
        if tok.isdigit() and int(tok) in by_num:
            k = by_num[int(tok)]
            if k not in keys:
                keys.append(k)
            continue
        raise SystemExit(f"노트북을 해석할 수 없습니다: {tok!r} "
                         f"(이름·번호·.ipynb 경로 / 발견된 것: {', '.join(sorted(available))})")
    return keys


def _has_any_outputs(nb: dict) -> bool:
    return any(c.get("cell_type") == "code" and c.get("outputs") for c in nb.get("cells", []))


def base_name(name: str) -> str:
    """직접 경로로 _executed 파일을 줬을 때를 대비해 접미사를 떼어 소스 이름을 얻는다."""
    return name[: -len(EXECUTED_SUFFIX)] if name.endswith(EXECUTED_SUFFIX) else name


def pick_source_notebook(name: str, nb_path: Path, root: Path, args) -> tuple[dict, str]:
    """출력 원천 선택. executed/ 폴더 대신 소스 옆 <stem>_executed.ipynb 규약을 쓴다."""
    if args.executed_notebook:
        p = Path(args.executed_notebook)
        p = p if p.is_absolute() else root / p
        return json.loads(p.read_text(encoding="utf-8")), f"executed-notebook({p.name})"
    # 소스 옆 <stem>_executed.ipynb (한 번 실행해 붙여둔 결과)
    sibling = nb_path.parent / f"{base_name(name)}{EXECUTED_SUFFIX}.ipynb"
    if sibling != nb_path and sibling.exists():
        return json.loads(sibling.read_text(encoding="utf-8")), sibling.name
    if args.execute:
        nb = execute_notebook(nb_path, timeout=args.timeout)
        saved = ""
        if args.save_executed:
            out = nb_path.parent / f"{base_name(name)}{EXECUTED_SUFFIX}.ipynb"
            out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
            saved = f" ({out.name} 저장됨)"
        return nb, "live --execute" + saved
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    return nb, ("노트북 자체 출력" if _has_any_outputs(nb) else "출력없음")


def load_config(path: Path | None) -> dict:
    cfg = {
        "book_title": DEFAULT_BOOK_TITLE,
        "split": "single",
        "section_mode": "keyword",
        "section_rules": [tuple(r) for r in DEFAULT_SECTION_RULES],
        "subpages": [tuple(s) for s in DEFAULT_SUBPAGES],
        "no_truncate": [],
        "labels": dict(DEFAULT_LABELS),
    }
    if path:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "section_rules" in raw:
            raw["section_rules"] = [tuple(r) for r in raw["section_rules"]]
        if "subpages" in raw:
            raw["subpages"] = [tuple(s) for s in raw["subpages"]]
        if "labels" in raw:
            merged = dict(DEFAULT_LABELS)
            merged.update(raw["labels"])
            raw["labels"] = merged
        cfg.update(raw)
    return cfg


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notebooks", nargs="*",
                    help="변환할 노트북(.ipynb 경로 / 이름 / 번호). 비우고 --all 로 전체 지정.")
    ap.add_argument("--all", action="store_true", help="--root 아래 발견된 모든 노트북을 변환")
    ap.add_argument("--root", default=".", help="프로젝트 루트(기본: 현재 디렉터리)")
    ap.add_argument("--config", default=None, help="분할/라벨/제목 설정 JSON 경로")
    ap.add_argument("--split", choices=("single", "sections"), default=None,
                    help="single(기본): 노트북=페이지 / sections: 장→절 분할"
                         "(config 없으면 H2 헤딩 단위, config 의 section_rules 가 있으면 키워드 분할)")
    ap.add_argument("--pages-dir", default="pages")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--toc", default="TOC.md")
    ap.add_argument("--book-title", default=None)
    ap.add_argument("--output-style", choices=OUTPUT_STYLES, default=DEFAULT_OUTPUT_STYLE,
                    help="실행 결과 박스: code(기본, 웹·PDF·EPUB 안전) | html-box(웹 전용, 전자책 깨짐)")
    ap.add_argument("--execute", action="store_true",
                    help="표준 라이브러리로 코드 셀을 실행해 실제 출력을 채움 (CPU 노트북용; GPU는 colab-cli 권장)")
    ap.add_argument("--executed-notebook", default=None, help="(단일) 출력이 담긴 실행본 .ipynb 경로")
    ap.add_argument("--save-executed", action="store_true",
                    help="--execute 결과를 소스 옆 <이름>_executed.ipynb 로 저장")
    ap.add_argument("--no-truncate", nargs="*", default=None,
                    help="긴 산문 트렁케이트를 끄는 노트북 이름들(토큰화 등 출력=학습내용)")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    cfg = load_config(Path(args.config).expanduser() if args.config else None)
    if args.split:
        cfg["split"] = args.split
    # config 파일이 없으면 sections 는 H2 헤딩 단위 구조적 분할(키워드 불필요).
    # config 로 section_rules 를 주면 키워드 분할(레거시 호환).
    cfg["section_mode"] = "keyword" if args.config else "structural"
    if args.no_truncate is not None:
        cfg["no_truncate"] = args.no_truncate
    book_title = args.book_title or cfg["book_title"]

    def _abs(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else root / pp

    pages_dir = _abs(args.pages_dir)
    assets_dir = _abs(args.assets) if args.assets else None
    toc_path = _abs(args.toc)

    available = discover_notebooks(root)
    if not available and not (args.notebooks and any(t.endswith(".ipynb") for t in args.notebooks)):
        raise SystemExit(f"변환할 노트북을 찾지 못했습니다 (root={root})")

    if args.notebooks:
        selected = parse_chapter_args(args.notebooks, available, root)
    elif args.all:
        selected = sorted(available, key=lambda k: (available[k][0] is None, available[k][0] or 0, k))
    else:
        raise SystemExit(
            "변환할 노트북을 지정하거나 --all 을 주세요.\n"
            f"  발견된 노트북: {', '.join(sorted(available))}"
        )

    if args.executed_notebook and len(selected) != 1:
        raise SystemExit("--executed-notebook 은 노트북 1개만 지정했을 때 씁니다.")

    print(f"변환 대상 {len(selected)}개 (split={cfg['split']}): {', '.join(selected)}\n")
    ok, failed = [], []
    for name in selected:
        num, slug, nb_path = available[name]
        try:
            nb, source = pick_source_notebook(name, nb_path, root, args)
            title = resolve_title(num, slug, nb)
            entries, stats = convert(nb, num, name, slug, title, pages_dir, assets_dir, cfg,
                                     args.output_style)
            upsert_toc(toc_path, book_title, num, name, entries)
            print(f"[{name}] {title}")
            print(f"     원천={source}  코드셀 {stats['code_cells']}개 "
                  f"(실제출력 {stats['code_with_output']} / 출력없음 {stats['no_output']}) "
                  f"이미지 {stats['images']}")
            if cfg["split"] == "sections":
                ns = stats.get("sections", 0)
                if cfg.get("section_mode") == "structural" and ns == 0:
                    print("     ⚠ H2(## …) 헤딩이 없어 절 분할이 안 됨 — 개요 1페이지만 생성"
                          "(노트북에 ## 헤딩을 넣거나 --split single 사용)")
                else:
                    print(f"     절 {ns}개로 분할")
            fixes = []
            for key, lbl in (("hr_removed", "수평선"), ("h1_demoted", "H1→H2"),
                             ("footnotes", "각주"), ("heading_blanks", "헤딩 빈 줄"),
                             ("win_paths", "윈도우 경로")):
                if stats.get(key):
                    fixes.append(f"{lbl} {stats[key]}")
            if fixes:
                print("     방어(전자책 규칙):", " / ".join(fixes))
            if stats["html_warn"]:
                print(f"     ⚠ raw HTML {len(stats['html_warn'])}건(전자책 깨질 수 있음): {stats['html_warn'][:4]}")
            if stats["extimg_warn"]:
                print(f"     ⚠ 외부 이미지 {len(stats['extimg_warn'])}건(PDF 누락 위험): {stats['extimg_warn'][:3]}")
            ok.append(name)
        except Exception as e:
            failed.append((name, e))
            print(f"[{name}] 실패: {e}")
            traceback.print_exc(limit=2)

    print(f"\n완료: 성공 {len(ok)} / 실패 {len(failed)}")
    if failed:
        print("실패: " + ", ".join(n for n, _ in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
