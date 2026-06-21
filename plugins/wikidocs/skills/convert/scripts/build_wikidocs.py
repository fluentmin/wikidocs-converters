#!/usr/bin/env python3
"""Jupyter 노트북(.ipynb)을 WikiDocs 연동용 마크다운으로 변환한다.

핵심 차이는 단순 파싱이 아니라 **코드의 실제 실행 결과(표·로그·그림)를 함께 싣는다**는 점이다.
노트북은 보통 출력이 비어 있으므로(배포용 clean 상태), 다음 우선순위로 "실제 결과"를 확보한다.

출력 원천 우선순위 (노트북별 자동):
  1) --executed-notebook PATH    : (단일 노트북) 미리 실행해 outputs를 담은 노트북
  2) <stem>_executed.ipynb       : 소스 노트북 옆에 같은 이름+_executed 가 있으면 자동 사용
                                   (실행 결과 없는 노트북을 한 번 실행해 붙여둔 결과물)
  3) 노트북 자체의 outputs        : 입력 노트북에 이미 출력이 박혀 있으면 그대로 사용
  4) (없음)                      : 코드만 싣는다(가짜 출력 금지). 노트북 전체에 출력이 없으면
                                   원천=출력없음 으로 표시되어 ①에서 사용자에게 실행 여부를 묻는다.

실행은 이 변환기가 직접 하지 않는다 — colab-cli 러너(run_via_cli.sh)가 Colab 에서 돌려
<stem>_executed.ipynb 를 만든다. executed/ 같은 별도 보관 폴더는 쓰지 않는다 —
실행본은 소스 옆에 _executed 로 둔다.

분할(장→절):
  (기본, --split 없음)   : 노트북 1개 = 페이지 1개. 의존성·설정 없이 동작.
  --split (값 없이)      : sections 와 동일 — 분할 켜기.
  --split sections       : 한 장을 여러 절로 분할.
                           · config 없음 → H2(## …) 헤딩 단위 구조적 분할(키워드 불필요).
                           · --config 의 section_rules → 키워드로 고정 버킷 분할(레거시).
  --split single         : 명시적으로 분할 끄기(기본과 동일).

노트북 지정:
  - 위치 인자로 .ipynb 경로를 직접 주거나, 번호/이름(--root 아래에서 발견)으로 줄 수 있다.
  - --all 이면 --root 아래의 .ipynb 를 자동 발견(<이름>_executed·체크포인트·러너는 제외).

사용:
  python3 build_wikidocs.py path/to/notebook.ipynb
  python3 build_wikidocs.py report.ipynb --split               # H2 헤딩 단위 분할(= --split sections)
  python3 build_wikidocs.py --all --root ~/proj

전자책 안전 출력·TOC·헤딩 정리 등 포맷 무관 코어는 wikidocs_common 에서 가져온다(정적 문서
변환기 convert_markdown.py 등과 공유). 이 파일은 노트북 고유 부분(셀 출력 렌더·실행본 선택·
키워드 분할)만 담는다.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import traceback
from pathlib import Path

from wikidocs_common import (
    DEFAULT_BOOK_TITLE, DEFAULT_LABELS, DEFAULT_OUTPUT_STYLE, OUTPUT_STYLES,
    H1_CHAPTER_PREFIX_RE, LEADING_NUM_RE,
    _clean_heading_text, _clean_text_output, _demote_first_header, _first_header,
    _html_tables_to_text, _output_box, _sanitize_md_cell, _strip_colab_badge,
    _strip_header_emoji, upsert_toc,
)

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


# --------------------------------------------------------------------------- #
# 노트북 셀 유틸
# --------------------------------------------------------------------------- #
def _cell_text(cell: dict) -> str:
    src = cell.get("source", "")
    return src if isinstance(src, str) else "".join(src)


def _classify(header_text: str, section_rules: list[tuple[str, str]]) -> str:
    for kw, group in section_rules:
        if kw in header_text:
            return group
    return "overview"


# --------------------------------------------------------------------------- #
# 셀 출력 렌더링 (노트북 고유)
# --------------------------------------------------------------------------- #
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


def chapter_h1_title(nb: dict) -> str:
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        m = _first_header(_cell_text(cell))
        if m and m[0] == 1:
            return H1_CHAPTER_PREFIX_RE.sub("", m[1]).strip()
    return ""


# --------------------------------------------------------------------------- #
# 변환 (노트북)
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
# 노트북 발견 / 선택
# --------------------------------------------------------------------------- #
RUNNER_NAMES = {"run_on_colab", "run_via_cli", "colab_cli_exec"}
EXECUTED_SUFFIX = "_executed"  # 실행본 명명 규약: <소스>_executed.ipynb


def discover_notebooks(root: Path) -> dict[str, tuple[int | None, str, Path]]:
    """{name: (num, slug, nb_path)} — root 아래 .ipynb 자동 발견(하위 폴더까지 재귀).

    1) 폴더 규약: NN_slug/NN_slug.ipynb (폴더와 노트북 이름이 같을 때) — 루트 직속 우선
    2) 그 외 임의 위치의 .ipynb (루트 직속·하위 폴더 모두; 러너·체크포인트·산출물 폴더 제외)
    실행본(<이름>_executed.ipynb)은 소스가 아니므로 발견 대상에서 제외한다.
    같은 stem 이 여러 곳이면 경로 정렬상 먼저 오는 것을 쓴다.
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

    # 루트 직속 폴더 규약 우선(NN_slug/NN_slug.ipynb 가 stem 충돌 시 이기도록 먼저 등록)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in ("assets", "pages") or d.name.startswith("."):
            continue
        nb = d / f"{d.name}.ipynb"
        if nb.exists():
            add(nb)
    # 루트 직속 + 하위 폴더 재귀 .ipynb (숨김·체크포인트·산출물 폴더 제외)
    for nb in sorted(root.rglob("*.ipynb")):
        rel = nb.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):   # .ipynb_checkpoints·.git 등
            continue
        if rel.parts and rel.parts[0] in ("assets", "pages"):
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
    ap.add_argument("--split", nargs="?", choices=("single", "sections"),
                    const="sections", default=None,
                    help="장→절 분할 켜기. 값 없이 --split 만 주면 sections 와 동일."
                         " single(기본): 노트북=페이지 / sections: H2(## …) 헤딩 단위 분할"
                         "(config 의 section_rules 가 있으면 키워드 분할)")
    ap.add_argument("--pages-dir", default="pages")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--toc", default="TOC.md")
    ap.add_argument("--book-title", default=None)
    ap.add_argument("--output-style", choices=OUTPUT_STYLES, default=DEFAULT_OUTPUT_STYLE,
                    help="실행 결과 박스: code(기본, 웹·PDF·EPUB 안전) | html-box(웹 전용, 전자책 깨짐)")
    ap.add_argument("--executed-notebook", default=None, help="(단일) 출력이 담긴 실행본 .ipynb 경로")
    ap.add_argument("--no-truncate", nargs="*", default=None,
                    help="긴 산문 트렁케이트를 끄는 노트북 이름들(토큰화 등 출력=학습내용)")
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
