---
name: notebook-to-wikidocs
description: Jupyter 노트북(.ipynb)을 WikiDocs용 마크다운으로 변환한다. 코드와 함께 실제 실행 결과(표·로그·그림)를 싣고, 웹·PDF·EPUB(전자책) 어디서도 깨지지 않게 만든다. 실행본이 없으면 google-colab-cli로 실행해 결과를 확보한다.
argument-hint: "[노트북] 예: notebook.ipynb · 7 · 07_bert_pipeline · --all"
disable-model-invocation: true
---

# notebook → WikiDocs 변환

노트북(.ipynb)을 WikiDocs 연동용 `pages/*.md` + 그림 `assets/` + 목차 `TOC.md` 로 바꾼다.
**핵심은 단순 파싱이 아니라 코드의 실제 실행 결과까지 싣는 것**이다.

**호출**: 사용자가 **변환할 노트북을 인자로** 주며 직접 호출한다(모델 자동 호출 금지 — 과금/장시간 실행).
```
/notebook-to-wikidocs path/to/notebook.ipynb
/notebook-to-wikidocs 7 24                 # 번호로
/notebook-to-wikidocs 07_bert_pipeline     # 이름/폴더명으로
/notebook-to-wikidocs --all                # 전체 (사용자 확인 후)
```

**핵심 원칙**
- 코드를 실으면 그 코드의 **실제 실행 결과**도 함께 싣는다 — 가짜 출력을 지어내지 않는다.
  실행 결과가 없으면 코드만 두고 `<!-- 실행 결과 없음 -->` 주석으로 누락을 드러낸다.
- 같은 `.md` 가 **웹(WikiDocs)·PDF·EPUB 세 타깃** 어디서도 깨지지 않게 한다(서점 판매엔 EPUB 필수).

스크립트는 `scripts/`, 분할/라벨/제목 설정 예시는 `config/` 에 있다.
경로는 `${CLAUDE_PLUGIN_ROOT}` 또는 이 스킬 폴더 기준으로 잡는다.

## 파이프라인

`① 실행 결과 확보 → ② 변환(build_wikidocs.py) → ③ 검증(check_wikidocs_md.py) → ④ 결과 해석 덧붙이기`

대상 프로젝트(노트북이 있는 곳)는 `--root` 로 지정한다(기본: 현재 디렉터리). 산출물(`pages/`,
`assets/`, `TOC.md`, `executed/`)도 `--root` 아래에 만들어진다.

### ① 실행 결과 확보 — `executed/<이름>.ipynb`

노트북의 진짜 출력은 실행해야 나온다. 두 경로 모두 `executed/<이름>.ipynb` 를 만든다.

- **colab-cli (권장)** — [`google-colab-cli`](https://github.com/googlecolab/google-colab-cli) 로
  **터미널에서** VM 할당→실행→회수. 결과를 로컬로 받아 **PAT 불필요**, 인증 1회면 스킬이 직접 실행.
  ```bash
  # 사전 1회 (issue #14 keep-alive 수정본 — PyPI v0.5.11 이하는 VM 이 ~11분에 idle-prune)
  uv tool install "git+https://github.com/googlecolab/google-colab-cli"
  colab --auth=oauth2 whoami       # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.

  bash scripts/run_via_cli.sh --root <프로젝트>   # 인자 없으면 전부, '7 24' 처럼 일부도
  ```
  REPO 는 git origin 에서 자동 인식(VM 은 clone 만). `executed/<이름>.ipynb` 가 로컬에 쌓인다.
- **로컬 실행(CPU 노트북)** — GPU가 필요 없으면 변환 시 `--execute --save-executed` 로 nbclient 직접 실행.
- **수동** — Colab/Jupyter 에서 끝까지 실행 후 출력 포함 `.ipynb` 를 `executed/<이름>.ipynb` 로 저장.

**변환 전 반드시 확인**: 변환할 노트북의 `executed/<이름>.ipynb` 가 있는지 먼저 본다.
**없으면 합성으로 조용히 넘어가지 말고** 먼저 실행해 실행본을 만든다.

### ② 변환 — `scripts/build_wikidocs.py`

```bash
python3 scripts/build_wikidocs.py path/to/notebook.ipynb --root <프로젝트>
python3 scripts/build_wikidocs.py 7 24 --root <프로젝트>           # executed/<이름>.ipynb 자동 사용
python3 scripts/build_wikidocs.py --all --root <프로젝트>          # 전체 (사용자 확인 후)
```

**출력 원천 우선순위**(노트북별 자동): `--executed-notebook` > `executed/<이름>.ipynb` > `--execute` > (없음).

**분할(장→절)** — 기본은 단순화돼 있다:
- `--split single` **(기본)**: 노트북 1개 = 페이지 1개. 설정·의존성 없이 동작.
- `--split sections`: `--config` 의 `section_rules`/`subpages` 로 한 장을 여러 절(실습/해부/변형/정리 등)로
  나눈다. 한글 커리큘럼 예시는 `config/neuqes-101.json` 참고 — 자기 키워드·라벨로 복사해 쓴다.

**전자책 안전 출력**(자동) — [wikidocs 전자책 작성시 주의할 점](https://wikidocs.net/198723) 기준:
출력 스타일 `code`(웹·PDF·EPUB 모두 안전), 헤딩 위아래 빈 줄, 수평선 제거, 본문 H1→H2 강등,
각주 이름 유니크화, 윈도우 경로 인라인 코드화, ML 노이즈 필터(HF Hub·tqdm·생성 보일러플레이트),
EPUB 긴 산문 줄 트렁케이트(표는 보존). 트렁케이트 제외는 `--no-truncate <이름...>` 또는 config `no_truncate`.

산출물: `pages/<이름>.md`(+ `-{practice,anatomy,variation,wrapup}.md`), 그림 `assets/<이름>-outK.png`,
`TOC.md`(해당 항목 블록만 교체). 노트북별 실패는 격리되어 배치를 멈추지 않는다.

### ③ 검증 — `scripts/check_wikidocs_md.py`

```bash
python3 scripts/check_wikidocs_md.py --root <프로젝트>   # pages/*.md 전수 검사. 위반 시 종료코드 1
```

변환기가 자동 방어하지만, **회귀·수기 편집**을 잡는 독립 린터다(코드펜스 안 제외).
이어서 사람이 확인: 코드 셀에 `▶ 실행 결과`(또는 `<!-- 실행 결과 없음 -->`이 맞는지),
`assets/` PNG·상대경로(`../assets/...`), 첫 H1 제거(페이지 제목은 `TOC.md` 담당).

### ④ 결과 해석 덧붙이기 (스킬이 직접 작성 — 스크립트 아님)

②변환·③린터를 통과한 뒤, 생성된 `pages/*.md` 의 **의미 있는 실행 결과**(`▶ 실행 결과`) 뒤에
짧은 **결과 해석**을 덧붙인다.

- 근거: 노트북의 마크다운 셀 설명 + 실제 출력. 거기 없는 새 사실을 지어내지 않는다.
- **기존 내용은 삭제·수정하지 않는다** — 출력 블록 뒤에 머릿말 `**결과 해석**` 을 붙여 **추가만** 한다.
- **형식 고정**: `**결과 해석**` 을 **단독 줄**로 두고 위·아래 빈 줄 + 그다음 줄부터 본문.
- 대상: 해석이 도움 되는 출력(학습 지표·분류 결과·표·생성 샘플 등). import 로그 등 사소한 출력은 건너뛴다.
- 분량·톤: **1~2문장**으로 간략히, 전자책 톤(존댓말·간결).
- **노트북 산문의 추정치와 실행본 실제값이 다르면 실제값 기준으로 해석**하고, 그 불일치는 사용자에게 보고한다.

## 주의

- colab-cli 는 외부 도구라 플러그인이 번들하지 않는다 — 위 사전 준비 1회 필요. **결제수단 없는 무료 계정 권장**.
- 한 번에 한 노트북씩, 검증 안 된 것을 두고 다음으로 넘어가지 않는다.
- 모델 자동 호출 금지(`disable-model-invocation: true`) — 과금/장시간 실행이라 사용자가 직접 호출한다.
