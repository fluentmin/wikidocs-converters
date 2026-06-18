#!/usr/bin/env bash
# 노트북 실행본(executed/)을 google-colab-cli 로 로컬에서 자동 생성한다 (macOS/Linux 전용).
#
# 노트북마다 새 Colab VM 할당 → 실행 → 결과 회수를 터미널 한 줄로 한다. 결과를 로컬
# executed/ 로 받으므로 **GitHub PAT 가 필요 없다**(평소 git 으로 커밋).
#
# 사용 (PROJECT_ROOT 미지정 시 현재 git 저장소 루트, REPO 는 git origin 에서 자동 인식)
#   ./run_via_cli.sh                       # 전 노트북
#   ./run_via_cli.sh 7 24                  # 해당 것만 (번호/폴더명/이름)
#   ./run_via_cli.sh 07_bert_pipeline
#   FORCE=1 ./run_via_cli.sh 7             # 로컬에 ok 여도 강제 재실행
#   GPU=L4 ./run_via_cli.sh                # GPU 종류 변경
#   PROJECT_ROOT=~/proj ./run_via_cli.sh   # 루트 직접 지정(기본: git rev-parse --show-toplevel)
#   REPO=other/repo ./run_via_cli.sh       # origin 외 다른 저장소를 쓸 때만
#   BRANCH=main ./run_via_cli.sh           # 기본 master
#
# 동작
#   - VM 은 노트북마다 **격리**(한 노트북 실패가 다음에 안 번짐). resume: 로컬에 status=ok 면 skip.
#   - 일시 드롭(Connection lost)은 노트북당 1회 자동 재시도. 끝나면 VM 자동 종료(유닛 절약).
#
# 사전 준비(최초 1회)
#   1) 설치:  uv tool install "git+https://github.com/googlecolab/google-colab-cli"
#        ※ issue #14(keep-alive) 수정 포함 버전 필요. PyPI v0.5.11 이하는 VM 이 ~11분에 idle-prune 됨.
#   2) 인증:  colab --auth=oauth2 whoami     # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"           # 이 스크립트(스킬 scripts/)
EXECPY="$HERE/colab_cli_exec.py"

# 프로젝트 루트: 명시 없으면 현재 git 저장소 루트, 그것도 없으면 cwd.
ROOT="${PROJECT_ROOT:-}"
if [ -z "$ROOT" ]; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
ROOT="$(cd "$ROOT" && pwd)"
OUTDIR="$ROOT/executed"

# REPO: 미지정 시 로컬 git origin 에서 자동 인식(VM 은 clone 만 하므로 origin 이면 충분).
REPO="${REPO:-}"
if [ -z "$REPO" ]; then
    REPO="$(git -C "$ROOT" remote get-url origin 2>/dev/null | sed -E 's#\.git/?$##; s#^.*github\.com[:/]+##')"
fi
BRANCH="${BRANCH:-master}"
GPU="${GPU:-T4}"
FORCE="${FORCE:-}"

printf '%s' "$REPO" | grep -qE '^[^/]+/[^/]+$' || {
    echo "✗ REPO 를 자동 인식하지 못했습니다(git origin 확인). 직접 지정: REPO=you/repo $0 [노트북...]"; exit 1; }
command -v colab >/dev/null 2>&1 || { echo "✗ colab-cli 미설치 → uv tool install \"git+https://github.com/googlecolab/google-colab-cli\""; exit 1; }
[ -f "$EXECPY" ] || { echo "✗ $EXECPY 없음(같은 폴더의 colab_cli_exec.py 필요)"; exit 1; }
mkdir -p "$OUTDIR"

CUR_SESSION=""
cleanup() { [ -n "$CUR_SESSION" ] && colab stop -s "$CUR_SESSION" >/dev/null 2>&1 || true; }
trap cleanup EXIT

is_ok() {
    python3 -c 'import json,sys
try:
    nb=json.load(open(sys.argv[1])); sys.exit(0 if nb["metadata"].get("executed_from",{}).get("status")=="ok" else 1)
except Exception: sys.exit(1)' "$1" 2>/dev/null
}

# 인자(번호/폴더명/이름) → 실행 대상 이름(노트북 stem). 못 찾으면 1.
resolve() {
    local spec="$1" nn m
    [ -d "$ROOT/$spec" ] && { echo "$spec"; return 0; }                  # NN_slug/ 폴더
    [ -f "$ROOT/$spec.ipynb" ] && { echo "$spec"; return 0; }            # 루트 직속 이름.ipynb
    if printf '%s' "$spec" | grep -qE '^[0-9]+$'; then
        nn=$(printf '%02d' "$spec")
        m=$(cd "$ROOT" && { ls -d "${nn}"_*/ 2>/dev/null | head -1 | sed 's#/##'; \
                            ls "${nn}"_*.ipynb 2>/dev/null | head -1 | sed 's#\.ipynb$##'; } | head -1)
        [ -n "$m" ] && { echo "$m"; return 0; }
    fi
    return 1
}

# 전체 대상 목록(이름): NN_slug/ 폴더 + 루트 직속 .ipynb(러너 제외).
all_targets() {
    (cd "$ROOT" && ls -d [0-9]*_*/ 2>/dev/null | sed 's#/##'
     cd "$ROOT" && ls *.ipynb 2>/dev/null | sed 's#\.ipynb$##' \
        | grep -vE '^(run_on_colab|run_via_cli|colab_cli_exec)$') | sort -u
}

run_one() {  # $1=name — 새 VM 1개로 실행 → executed/<name>.ipynb 회수 → VM 종료
    local name="$1" sess b64
    sess="wd-$(echo "$name" | tr '_' '-')"
    CUR_SESSION="$sess"
    colab run --keep -s "$sess" --gpu "$GPU" --timeout 120 \
        "$EXECPY" "$REPO" "$BRANCH" "$name" --force \
        || echo "  (colab run 비정상 종료)"
    b64="$(mktemp)"
    colab download -s "$sess" "/content/${REPO##*/}/executed/$name.ipynb.b64" "$b64" >/dev/null 2>&1 \
        && python3 -c 'import base64,sys;open(sys.argv[2],"wb").write(base64.b64decode(open(sys.argv[1],"rb").read()))' "$b64" "$OUTDIR/$name.ipynb" \
        || true
    rm -f "$b64"
    colab stop -s "$sess" >/dev/null 2>&1 || true
    CUR_SESSION=""
}

targets=()
if [ "$#" -gt 0 ]; then
    for spec in "$@"; do
        if f=$(resolve "$spec"); then targets+=("$f"); else echo "⚠️ 알 수 없는 대상: $spec (건너뜀)"; fi
    done
else
    while IFS= read -r f; do [ -n "$f" ] && targets+=("$f"); done < <(all_targets)
fi
total=${#targets[@]}
[ "$total" -gt 0 ] || { echo "✗ 대상이 없습니다(인자 확인, 또는 ROOT 에 NN_slug/ 또는 *.ipynb 가 있는지)"; exit 1; }

echo "===== CLI 실행 (${total}개, repo=$REPO@$BRANCH, root=$ROOT, $(date '+%H:%M:%S')) ====="
ok=0; skip=0; fail=0; i=0
for name in "${targets[@]}"; do
    i=$((i + 1))
    if [ -z "$FORCE" ] && is_ok "$OUTDIR/$name.ipynb"; then
        echo "[$i/$total] $name — 이미 ok, skip (FORCE=1 로 강제)"; skip=$((skip + 1)); continue
    fi
    echo ""
    echo "===== [$i/$total] $name — 새 VM ($(date '+%H:%M:%S')) ====="
    run_one "$name"
    if ! is_ok "$OUTDIR/$name.ipynb"; then
        echo "  ↻ $name 1차 미완 — 1회 재시도 ($(date '+%H:%M:%S'))"
        run_one "$name"
    fi
    if is_ok "$OUTDIR/$name.ipynb"; then
        echo "  ✓ $name"; ok=$((ok + 1))
    else
        echo "  ✗ $name — 미완(11분 초과 또는 연결 끊김; 2회 시도)"; fail=$((fail + 1))
    fi
done

echo ""
echo "===== 종료 ($(date '+%H:%M:%S')): ok=$ok skip=$skip fail=$fail / total=$total ====="
echo "이어서 변환:  python3 \"$HERE/build_wikidocs.py\" --root \"$ROOT\" <대상>"
