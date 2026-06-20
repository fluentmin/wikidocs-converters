#!/usr/bin/env python3
"""Colab VM 위에서 도는 실행본 생성기 — colab-cli `colab run` 의 대상 스크립트.

로컬 래퍼 `run_via_cli.sh` 가 호출한다(직접 실행할 일은 없음):

    colab run --keep -s <세션> --gpu T4 --timeout 120 \
        colab_cli_exec.py <REPO> <BRANCH> <TARGET> [--force] [--token TOKEN]

`colab run` 이 이 파일을 VM 커널에 올려 실행하면:
  1) REPO 를 clone (VM 은 clone 만 — 원본 push 권한 불필요).
     --token 이 오면 x-access-token 으로 주입해 private repo 도 clone(없으면 익명 HTTPS).
  2) 대상 노트북을 NotebookClient 로 끝까지 실행
  3) 출력 포함 <소스옆>/<이름>_executed.ipynb 를 VM 에 저장(실패해도 다음 노트북 계속)
  4) 무손실 회수용 base64 사본을 플랫 스테이징(/content/_wd_out/<이름>_executed.ipynb.b64) 에 저장
그 뒤 래퍼가 `colab download` 로 결과를 로컬 소스 옆에 가져온다.

별도 executed/ 보관 폴더는 쓰지 않는다 — 실행본은 소스 노트북 옆에 _executed 로 둔다.
소스 해시 멱등(`executed_from` 도장)으로 안 바뀐 노트북은 건너뛴다.

TARGET:
  stale          없거나 소스가 바뀐 노트북만 (기본)
  all            전부
  "1,7,foo"      쉼표 목록 — 번호 또는 이름
"""
import sys
import base64
import subprocess
import hashlib
import datetime
import time
import threading
from pathlib import Path

PER_CELL_TIMEOUT = 60 * 60  # 셀당 최대 실행 시간(초)
RUNNER_NAMES = {"run_on_colab", "run_via_cli", "colab_cli_exec"}
EXECUTED_SUFFIX = "_executed"
STAGING = "/content/_wd_out"  # b64 회수용 플랫 스테이징(래퍼가 여기서 download)


def fmt_dur(sec):
    m, s = divmod(int(round(sec)), 60)
    return f"{m}분 {s}초" if m else f"{s}초"


def main():
    if len(sys.argv) < 3:
        print("usage: colab_cli_exec.py <REPO> <BRANCH> [TARGET] [--force] [--token TOKEN]", file=sys.stderr)
        sys.exit(2)
    repo = sys.argv[1]
    branch = sys.argv[2]
    target = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "stale"
    force = "--force" in sys.argv
    # private repo clone 용 토큰(있을 때만). VM 은 익명 HTTPS 라 토큰 없이는 private 접근 불가.
    token = ""
    if "--token" in sys.argv:
        i = sys.argv.index("--token")
        if i + 1 < len(sys.argv):
            token = sys.argv[i + 1]

    subprocess.run([sys.executable, "-m", "pip", "-q", "install", "nbclient", "nbformat"], check=True)
    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    clone_url = (f"https://x-access-token:{token}@github.com/{repo}.git"
                 if token else f"https://github.com/{repo}.git")
    work = Path("/content") / repo.split("/")[-1]
    if not work.is_dir():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", branch,
                        clone_url, str(work)], check=True)
    else:
        subprocess.run(["git", "-C", str(work), "checkout", "-q", branch], check=True)
        subprocess.run(["git", "-C", str(work), "pull", "-q", "--depth", "1"], check=False)

    stage = Path(STAGING)
    stage.mkdir(parents=True, exist_ok=True)

    def executed_path(nb_path):
        """소스 노트북 옆 <이름>_executed.ipynb 경로."""
        return nb_path.parent / (nb_path.stem + EXECUTED_SUFFIX + ".ipynb")

    def notebooks():
        """[(name, nb_path)] — NN_slug/NN_slug.ipynb 폴더 규약 + 루트 직속 *.ipynb(러너·_executed 제외)."""
        out = {}
        for d in sorted(work.glob("[0-9]*_*")):
            if not d.is_dir():
                continue
            nb = d / (d.name + ".ipynb")
            if nb.exists():
                out[d.name] = nb
        for nb in sorted(work.glob("*.ipynb")):
            if nb.stem not in RUNNER_NAMES and not nb.stem.endswith(EXECUTED_SUFFIX):
                out.setdefault(nb.stem, nb)
        return list(out.items())

    def source_hash(nb_path):
        nb = nbformat.read(nb_path, as_version=4)
        h = hashlib.sha256()
        for c in nb.cells:
            h.update(c.cell_type.encode()); h.update(b"\0")
            h.update((c.source or "").encode()); h.update(b"\0")
        return h.hexdigest()

    def executed_hash(nb_path):
        p = executed_path(nb_path)
        if not p.exists():
            return None
        try:
            return nbformat.read(p, as_version=4).metadata.get("executed_from", {}).get("source_sha256")
        except Exception:
            return None

    all_nb = notebooks()
    print(f"발견한 노트북: {len(all_nb)}", flush=True)

    def base_set(t):
        if t in ("all", "stale"):
            return all_nb
        keys = {x.strip().zfill(2) if x.strip().isdigit() else x.strip()
                for x in t.split(",") if x.strip()}
        return [(n, p) for n, p in all_nb if n in keys or n[:2] in keys]

    base = base_set(target)
    sel = base if force else [(n, p) for n, p in base if source_hash(p) != executed_hash(p)]
    print(f"실행 대상: {len(sel)}개  (TARGET={target!r}, FORCE={force})", flush=True)

    written = []
    total_t0 = time.time()
    for name, p in sel:
        print(f"\n=== 실행: {name} ===", flush=True)
        nb = nbformat.read(p, as_version=4)
        client = NotebookClient(
            nb, timeout=PER_CELL_TIMEOUT, kernel_name="python3",
            resources={"metadata": {"path": str(p.parent)}}, allow_errors=False,
        )
        status = "ok"
        t0 = time.time()
        stop_hb = threading.Event()

        def _heartbeat():
            n = 0
            while not stop_hb.wait(20):
                n += 1
                print(f"  ··· {name} 실행 중 ({n * 20}s)", flush=True)

        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        try:
            client.execute()
        except CellExecutionError as e:
            status = "error: " + str(e).splitlines()[-1][:120]
            print("  ⚠️", status, flush=True)
        except Exception as e:
            status = "fail: " + str(e)[:120]
            print("  ⚠️", status, flush=True)
        finally:
            stop_hb.set()
        elapsed = time.time() - t0
        nb.metadata["executed_from"] = {
            "source_sha256": source_hash(p),
            "executed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "runtime": "colab",
            "status": status,
            "elapsed_sec": round(elapsed, 1),
        }
        # 소스 옆 <이름>_executed.ipynb + 회수용 b64(플랫 스테이징)
        out_local = executed_path(p)
        nbformat.write(nb, out_local)
        (stage / (name + EXECUTED_SUFFIX + ".ipynb.b64")).write_text(
            base64.b64encode(nbformat.writes(nb).encode("utf-8")).decode("ascii"))
        written.append((name, status, elapsed))
        print(f"  → 저장 {out_local.relative_to(work)}  [{status}]  ⏱ {fmt_dur(elapsed)}", flush=True)

    total_elapsed = time.time() - total_t0
    print("\n=== 요약 (소요 시간) ===", flush=True)
    for n, s, dt in written:
        print(f"  {n:<28}{fmt_dur(dt):>10}   {s}", flush=True)
    print(f"  {'합계 (' + str(len(written)) + '개)':<28}{fmt_dur(total_elapsed):>10}", flush=True)

    manifest = Path("/content/wikidocs_manifest.txt")
    manifest.write_text("\n".join(n for n, _, _ in written) + ("\n" if written else ""))
    print(f"\nMANIFEST {manifest} ({len(written)}개)", flush=True)


if __name__ == "__main__":
    main()
