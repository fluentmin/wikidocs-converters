#!/usr/bin/env python3
"""마크다운(.md) 문서를 WikiDocs 연동용 산출물로 변환한다.

.md 는 이미 마크다운이므로 변환 엔진이 필요 없다 — **읽어서 전자책 안전 코어로 정리**만 한다.
포맷 무관 처리(전자책 sanitize·H2 분할·이미지 로컬화·TOC upsert)는 모두 wikidocs_common 의
공용 코어를 그대로 쓴다(노트북 변환기와 동일 규약: pages/<이름>.md · assets/ · TOC.md).

핵심 동작:
  1) 입력 .md 읽기
  2) 본문의 외부/로컬 이미지를 assets/ 로 내려받거나 복사하고 `../assets/…` 로 치환
     (전자책 PDF/EPUB 은 외부 URL 이미지가 누락되므로 로컬 자산화가 필수)
  3) 전자책 규칙 sanitize(H1→H2 강등·헤딩 빈 줄·수평선 제거·각주 유니크화·윈도우 경로 등)
  4) --split 이면 H2(`## …`) 단위로 개요(+로드맵) + 절 서브페이지로 분할
  5) pages/*.md 기록 + TOC.md 의 해당 블록만 upsert

사용:
  python3 convert_markdown.py path/to/doc.md --root ~/proj
  python3 convert_markdown.py 07_intro.md --split            # H2 단위 분할
  python3 convert_markdown.py guide --root ~/proj            # 이름으로 찾기(<root>/**/guide.md)
  python3 convert_markdown.py --all --root ~/proj            # root 아래 모든 .md (산출물/숨김 폴더 제외)
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from wikidocs_common import (
    DEFAULT_BOOK_TITLE, LEADING_NUM_RE,
    emit_static_pages, localize_images, make_stem, markdown_title, new_stats,
)

# 변환 대상에서 제외할 산출물·메타 파일(우리가 만드는 것). 입력으로 다시 먹지 않는다.
SKIP_NAMES = {"TOC", "README", "readme", "index"}
SKIP_DIRS = ("pages", "assets")


def discover_markdown(root: Path) -> dict[str, Path]:
    """{name: md_path} — root 아래 .md 자동 발견(하위 폴더 재귀; 산출물·숨김 폴더 제외)."""
    found: dict[str, Path] = {}
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        if md.stem in SKIP_NAMES:
            continue
        found.setdefault(md.stem, md)
    return found


def resolve_inputs(tokens: list[str], available: dict[str, Path], root: Path) -> list[Path]:
    """'doc.md' / 'doc' / 'path/to/doc.md' → 실제 .md 경로 리스트."""
    paths: list[Path] = []
    for tok in tokens:
        p = Path(tok)
        if tok.endswith(".md"):
            pp = p if p.is_absolute() else root / p
            if not pp.exists():
                raise SystemExit(f"마크다운을 찾을 수 없습니다: {tok}")
            if pp not in paths:
                paths.append(pp)
            continue
        if tok in available:
            if available[tok] not in paths:
                paths.append(available[tok])
            continue
        raise SystemExit(f"마크다운을 해석할 수 없습니다: {tok!r} "
                         f"(이름·.md 경로 / 발견된 것: {', '.join(sorted(available)) or '없음'})")
    return paths


def convert_one(md_path: Path, *, pages_dir: Path, assets_dir: Path | None, toc_path: Path,
                book_title: str, split: bool, localize: bool) -> dict:
    name = md_path.stem
    m = LEADING_NUM_RE.match(name)
    num = int(m.group(1)) if m else None
    slug = m.group(2) if m else name
    stem = make_stem(name, num)

    raw = md_path.read_text(encoding="utf-8")
    title = markdown_title(raw, slug.replace("_", " ").replace("-", " "))
    stats = new_stats()

    if localize:
        raw = localize_images(raw, assets_dir, stem, md_path.parent, stats)

    emit_static_pages(raw, stem=stem, num=num, title=title, name=name,
                      pages_dir=pages_dir, toc_path=toc_path, book_title=book_title,
                      split=split, stats=stats)
    stats["_title"] = title
    stats["_name"] = name
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*",
                    help="변환할 마크다운(.md 경로 / 이름). 비우고 --all 로 전체 지정.")
    ap.add_argument("--all", action="store_true", help="--root 아래 발견된 모든 .md 를 변환")
    ap.add_argument("--root", default=".", help="프로젝트 루트(기본: 현재 디렉터리)")
    ap.add_argument("--split", action="store_true",
                    help="H2(## …) 헤딩 단위로 개요+절 분할(기본: 문서 1개=페이지 1개)")
    ap.add_argument("--pages-dir", default="pages")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--toc", default="TOC.md")
    ap.add_argument("--book-title", default=DEFAULT_BOOK_TITLE)
    ap.add_argument("--no-localize-images", action="store_true",
                    help="이미지 로컬화(외부 다운로드·복사)를 끈다. 외부 URL 이미지는 그대로 남아"
                         "전자책 PDF/EPUB 에서 누락될 수 있다.")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()

    def _abs(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else root / pp

    pages_dir = _abs(args.pages_dir)
    assets_dir = _abs(args.assets) if args.assets else None
    toc_path = _abs(args.toc)

    available = discover_markdown(root)
    if args.files:
        selected = resolve_inputs(args.files, available, root)
    elif args.all:
        selected = [available[k] for k in sorted(available)]
    else:
        raise SystemExit(
            "변환할 .md 를 지정하거나 --all 을 주세요.\n"
            f"  발견된 .md: {', '.join(sorted(available)) or '없음'}"
        )
    if not selected:
        raise SystemExit(f"변환할 .md 를 찾지 못했습니다 (root={root})")

    print(f"변환 대상 {len(selected)}개 (split={'sections' if args.split else 'single'}): "
          f"{', '.join(p.stem for p in selected)}\n")
    ok, failed = [], []
    for md_path in selected:
        try:
            stats = convert_one(md_path, pages_dir=pages_dir, assets_dir=assets_dir,
                                toc_path=toc_path, book_title=args.book_title,
                                split=args.split, localize=not args.no_localize_images)
            print(f"[{stats['_name']}] {stats['_title']}")
            info = f"     이미지 {stats['images']}"
            if args.split:
                ns = stats.get("sections", 0)
                if ns == 0:
                    print("     ⚠ H2(## …) 헤딩이 없어 절 분할이 안 됨 — 1페이지만 생성"
                          "(## 헤딩을 넣거나 --split 없이 실행)")
                else:
                    info += f"  절 {ns}개로 분할"
            print(info)
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
                print(f"     ⚠ 외부 이미지 {len(stats['extimg_warn'])}건(PDF 누락 위험): {stats['extimg_warn'][:3]}"
                      " — 로컬화 실패분일 수 있음")
            ok.append(md_path.stem)
        except Exception as e:
            failed.append((md_path.stem, e))
            print(f"[{md_path.stem}] 실패: {e}")
            traceback.print_exc(limit=2)

    print(f"\n완료: 성공 {len(ok)} / 실패 {len(failed)}")
    if failed:
        print("실패: " + ", ".join(n for n, _ in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
