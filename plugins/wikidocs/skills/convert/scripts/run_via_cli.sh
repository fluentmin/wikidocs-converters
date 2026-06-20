#!/usr/bin/env bash
# 노트북 실행본(executed/)을 google-colab-cli 로 로컬에서 자동 생성한다 (macOS/Linux 전용).
#
# 노트북마다 새 Colab VM 할당 → 실행 → 결과 회수를 터미널 한 줄로 한다. 결과는 로컬 소스 옆
# <이름>_executed.ipynb 로 받는다(평소 git 으로 커밋).
#
# **public repo 는 토큰 불필요.** private repo 는 VM 이 익명 HTTPS 로 clone 하므로 토큰이 필요하다 —
# GH_TOKEN/GITHUB_TOKEN 또는 `gh auth token`(gh 로그인돼 있으면 자동)을 찾아 clone 에 주입한다.
# 토큰이 없으면 VM 할당 전에 미리 막는다(과금 방지).
#
# 노트북은 루트 직속·NN_slug/ 폴더는 물론 하위 폴더(예: my-test-notebooks/foo.ipynb)도 재귀로 찾는다.
#
# 사용 (PROJECT_ROOT/--root 미지정 시 현재 git 저장소 루트, REPO 는 git origin 에서 자동 인식)
#   ./run_via_cli.sh                       # 전 노트북
#   ./run_via_cli.sh 7 24                  # 해당 것만 (번호/폴더명/이름)
#   ./run_via_cli.sh 07_bert_pipeline
#   ./run_via_cli.sh sub/dir/foo.ipynb     # 하위 폴더 경로도 가능
#   ./run_via_cli.sh --root ~/proj 7       # 루트 플래그(build/check 와 동일). PROJECT_ROOT 보다 우선
#   FORCE=1 ./run_via_cli.sh 7             # 로컬에 ok 여도 강제 재실행
#   GPU=L4 ./run_via_cli.sh                # GPU 종류 변경
#   PROJECT_ROOT=~/proj ./run_via_cli.sh   # 루트 직접 지정(기본: git rev-parse --show-toplevel)
#   REPO=other/repo ./run_via_cli.sh       # origin 외 다른 저장소를 쓸 때만
#   BRANCH=dev ./run_via_cli.sh            # 기본: 로컬 현재 브랜치 자동 인식(없으면 origin 기본→main)
#   GH_TOKEN=ghp_xxx ./run_via_cli.sh      # private repo: 토큰 직접 지정(미지정 시 gh auth token 사용)
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

# 인자에서 --root <path> 추출(나머지는 노트북 spec). build/check 스크립트와 플래그 일관성을 맞춘다
# (SKILL.md 가 --root 를 넘기므로 spec 으로 오인해 "알 수 없는 대상: --root" 나던 문제 방지).
ARG_ROOT=""
specs=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)   ARG_ROOT="${2:-}"; shift; [ "$#" -gt 0 ] && shift ;;
        --root=*) ARG_ROOT="${1#--root=}"; shift ;;
        *)        specs+=("$1"); shift ;;
    esac
done
set -- ${specs[@]+"${specs[@]}"}

# 프로젝트 루트: --root > PROJECT_ROOT > 현재 git 저장소 루트 > cwd.
ROOT="${ARG_ROOT:-${PROJECT_ROOT:-}}"
if [ -z "$ROOT" ]; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
ROOT="$(cd "$ROOT" && pwd)"
STAGING="/content/_wd_out"   # VM 플랫 스테이징(b64 회수 위치) — colab_cli_exec.py 와 일치

# REPO: 미지정 시 로컬 git origin 에서 자동 인식(VM 은 clone 만 하므로 origin 이면 충분).
REPO="${REPO:-}"
if [ -z "$REPO" ]; then
    REPO="$(git -C "$ROOT" remote get-url origin 2>/dev/null | sed -E 's#\.git/?$##; s#^.*github\.com[:/]+##')"
fi
# BRANCH: 미지정 시 로컬 현재 브랜치 → origin 기본 브랜치 → main 순으로 자동 인식.
# (저장소가 main 인데 master 로 고정하면 VM clone 이 exit 128 로 실패하던 문제 방지.)
BRANCH="${BRANCH:-}"
if [ -z "$BRANCH" ]; then
    BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
        BRANCH="$(git -C "$ROOT" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
    fi
    [ -z "$BRANCH" ] && BRANCH="main"
fi
GPU="${GPU:-T4}"
FORCE="${FORCE:-}"

printf '%s' "$REPO" | grep -qE '^[^/]+/[^/]+$' || {
    echo "✗ REPO 를 자동 인식하지 못했습니다(git origin 확인). 직접 지정: REPO=you/repo $0 [노트북...]"; exit 1; }
command -v colab >/dev/null 2>&1 || { echo "✗ colab-cli 미설치 → uv tool install \"git+https://github.com/googlecolab/google-colab-cli\""; exit 1; }
[ -f "$EXECPY" ] || { echo "✗ $EXECPY 없음(같은 폴더의 colab_cli_exec.py 필요)"; exit 1; }

# 토큰: 명시 GH_TOKEN/GITHUB_TOKEN → gh auth token 순. private repo clone 에만 쓴다.
# (VM 은 익명 HTTPS 라 토큰 없이는 private 접근 불가 → exit 128.)
# 권장: `gh auth login`(OS 키체인) — 그러면 토큰을 명령줄에 타이핑할 필요가 없고, 여기서 런타임에
#   변수로만 읽으므로 화면/로그·스킬 모델 컨텍스트에 토큰이 절대 출력되지 않는다(아래 run_one 에서도 마스킹).
# 주의: 토큰은 VM argv 에는 전달된다 — 사용자 소유 일회성 VM 에서만 쓰고, 가능하면 짧은 수명/최소
#   권한(read-only contents) 토큰을 권한다. 이 스크립트는 토큰을 stdout 으로 출력하지 않는다.
TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "$TOKEN" ] && command -v gh >/dev/null 2>&1; then
    TOKEN="$(gh auth token 2>/dev/null || true)"
fi

# private 사전 감지: VM 할당(과금) 전에 막는다. gh 로 isPrivate 확인되고 토큰이 없으면 fail-fast.
if command -v gh >/dev/null 2>&1; then
    IS_PRIVATE="$(gh repo view "$REPO" --json isPrivate -q .isPrivate 2>/dev/null || true)"
    if [ "$IS_PRIVATE" = "true" ] && [ -z "$TOKEN" ]; then
        echo "✗ $REPO 는 private 인데 clone 토큰이 없습니다. Colab VM 은 익명 HTTPS 로 clone 하므로 실패합니다."
        echo "  해결: 1) gh auth login 후 재실행(gh 토큰 자동 사용), 또는 GH_TOKEN=<토큰> $0 ..."
        echo "       2) 저장소를 잠시 public 으로: gh repo edit $REPO --visibility public  (끝나면 다시 private)"
        exit 1
    fi
fi

CUR_SESSION=""
cleanup() { [ -n "$CUR_SESSION" ] && colab stop -s "$CUR_SESSION" >/dev/null 2>&1 || true; }
trap cleanup EXIT

is_ok() {
    python3 -c 'import json,sys
try:
    nb=json.load(open(sys.argv[1])); sys.exit(0 if nb["metadata"].get("executed_from",{}).get("status")=="ok" else 1)
except Exception: sys.exit(1)' "$1" 2>/dev/null
}

# 로컬에서 stem(이름)에 해당하는 소스 노트북 파일 경로를 찾는다(하위 폴더까지 재귀).
# 숨김(.git·.ipynb_checkpoints)·러너·_executed 는 제외. 못 찾으면 빈 문자열.
find_nb() {
    local name="$1"
    [ -f "$ROOT/$name/$name.ipynb" ] && { echo "$ROOT/$name/$name.ipynb"; return; }   # NN_slug 폴더 우선
    [ -f "$ROOT/$name.ipynb" ] && { echo "$ROOT/$name.ipynb"; return; }                # 루트 직속
    find "$ROOT" -type f -name "$name.ipynb" -not -path '*/.*' 2>/dev/null | sort | head -1
}

# 로컬 소스 노트북이 있는 디렉터리(실행본은 그 옆에 <이름>_executed.ipynb 로 둔다).
src_dir() {
    local f; f="$(find_nb "$1")"
    if [ -n "$f" ]; then dirname "$f"; else echo "$ROOT"; fi
}
# 로컬 실행본 경로
exec_path() { echo "$(src_dir "$1")/${1}_executed.ipynb"; }

# 인자(번호/폴더명/이름/.ipynb 경로) → 실행 대상 이름(노트북 stem). 못 찾으면 1.
# 주의: VM 이 REPO 를 clone 해 '이름'으로 찾으므로, 대상 노트북은 푸시된 저장소 안에 있어야 한다.
resolve() {
    local spec="$1" nn base found
    case "$spec" in
        *.ipynb)                                                        # 절대/상대 .ipynb 경로
            base="$(basename "$spec" .ipynb)"; echo "$base"; return 0 ;;
    esac
    [ -d "$ROOT/$spec" ] && { echo "$spec"; return 0; }                  # NN_slug/ 폴더
    [ -n "$(find_nb "$spec")" ] && { echo "$spec"; return 0; }          # 이름.ipynb (루트·하위 폴더)
    if printf '%s' "$spec" | grep -qE '^[0-9]+$'; then
        nn=$(printf '%02d' "$spec")                                     # 번호 → NN_*.ipynb (하위 폴더 포함)
        found="$(find "$ROOT" -type f -name "${nn}_*.ipynb" -not -path '*/.*' -not -name '*_executed.ipynb' 2>/dev/null | sort | head -1)"
        [ -n "$found" ] && { basename "$found" .ipynb; return 0; }
    fi
    return 1
}

# 전체 대상 목록(이름, stem): ROOT 아래 모든 .ipynb 재귀(숨김·러너·_executed 제외).
all_targets() {
    find "$ROOT" -type f -name '*.ipynb' -not -path '*/.*' 2>/dev/null \
        | sed 's#.*/##; s#\.ipynb$##' \
        | grep -vE '^(run_on_colab|run_via_cli|colab_cli_exec)$|_executed$' \
        | sort -u
}

run_one() {  # $1=name — 새 VM 1개로 실행 → executed/<name>.ipynb 회수 → VM 종료
    local name="$1" sess b64
    sess="wd-$(echo "$name" | tr '_' '-')"
    CUR_SESSION="$sess"
    local tok_args=()
    [ -n "$TOKEN" ] && tok_args=(--token "$TOKEN")   # private repo 면 토큰 주입(없으면 익명 HTTPS)
    # 출력에서 토큰을 마스킹한다 — colab-cli 가 argv 를 echo 해도 토큰이 화면/로그(그리고 스킬을
    # 구동하는 모델의 컨텍스트)로 새지 않게. 토큰 없으면 cat 으로 그대로 통과.
    local mask="cat"
    [ -n "$TOKEN" ] && mask="sed s#${TOKEN}#***#g"   # 토큰은 [A-Za-z0-9_] 라 # 구분자/공백 안전
    # 빈 배열 확장은 bash 3.2(macOS 기본)+set -u 에서 unbound 오류 → +확장으로 가드.
    colab run --keep -s "$sess" --gpu "$GPU" --timeout 120 \
        "$EXECPY" "$REPO" "$BRANCH" "$name" --force ${tok_args[@]+"${tok_args[@]}"} 2>&1 \
        | $mask || echo "  (colab run 비정상 종료)"
    b64="$(mktemp)"; local dest; dest="$(exec_path "$name")"
    colab download -s "$sess" "$STAGING/${name}_executed.ipynb.b64" "$b64" >/dev/null 2>&1 \
        && python3 -c 'import base64,sys;open(sys.argv[2],"wb").write(base64.b64decode(open(sys.argv[1],"rb").read()))' "$b64" "$dest" \
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
    if [ -z "$FORCE" ] && is_ok "$(exec_path "$name")"; then
        echo "[$i/$total] $name — 이미 ok, skip (FORCE=1 로 강제)"; skip=$((skip + 1)); continue
    fi
    echo ""
    echo "===== [$i/$total] $name — 새 VM ($(date '+%H:%M:%S')) ====="
    run_one "$name"
    if ! is_ok "$(exec_path "$name")"; then
        echo "  ↻ $name 1차 미완 — 1회 재시도 ($(date '+%H:%M:%S'))"
        run_one "$name"
    fi
    if is_ok "$(exec_path "$name")"; then
        echo "  ✓ $name → $(exec_path "$name" | sed "s#^$ROOT/##")"; ok=$((ok + 1))
    else
        echo "  ✗ $name — 미완(11분 초과 또는 연결 끊김; 2회 시도)"; fail=$((fail + 1))
    fi
done

echo ""
echo "===== 종료 ($(date '+%H:%M:%S')): ok=$ok skip=$skip fail=$fail / total=$total ====="
echo "실행본은 소스 옆 <이름>_executed.ipynb 로 저장됨."
echo "이어서 변환:  python3 \"$HERE/build_wikidocs.py\" --root \"$ROOT\" <대상>"
