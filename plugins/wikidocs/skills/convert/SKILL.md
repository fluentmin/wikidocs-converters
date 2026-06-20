---
name: convert
description: 파일 확장자에 맞는 변환기를 골라 문서를 WikiDocs용 마크다운으로 변환한다. 웹·PDF·EPUB(전자책) 어디서도 깨지지 않게 만든다. 현재 Jupyter 노트북(.ipynb)을 지원하며, 코드와 함께 실제 실행 결과(표·로그·그림)까지 싣고 실행본이 없으면 google-colab-cli로 실행해 결과를 확보한다.
argument-hint: "<파일 경로(required), 예: report.ipynb(상대경로), /Users/path/to/report.ipynb(절대경로)> [--split(optional) (입력 시 변환 대상 파일을 장→절 여러 페이지로 분할, 미입력 시 하나의 페이지로 변환)] "
disable-model-invocation: true
---

# convert — 파일 → WikiDocs 변환

`/wikidocs:convert <파일>` 으로 **파일 확장자를 보고 알맞은 변환기를 골라** WikiDocs 연동용
`pages/*.md` + 그림 `assets/` + 목차 `TOC.md` 로 바꾼다.

**호출**: 사용자가 **변환할 파일을 인자로** 주며 직접 호출한다(모델 자동 호출 금지 — 과금/장시간 실행).
```
/wikidocs:convert path/to/report.ipynb
/wikidocs:convert 7 24                 # 번호로 (노트북 규약)
/wikidocs:convert 07_bert_pipeline     # 이름/폴더명으로
/wikidocs:convert --all                # 전체 (사용자 확인 후)
```

## 확장자 디스패치 (먼저 이걸로 분기)

인자로 받은 파일(또는 번호/이름으로 찾은 파일)의 **확장자**를 보고 변환 경로를 정한다.

| 확장자 | 처리 |
|---|---|
| `.ipynb` | **아래 "노트북(.ipynb) 파이프라인"** 으로 변환(현재 지원). |
| `.docx` · `.pdf` · `.md` · `.pptx` 등 | **아직 미지원** — 변환기가 없다. 사용자에게 "현재는 `.ipynb` 만 지원합니다. 이 포맷은 추후 추가될 예정입니다"라고 알리고 멈춘다(임의 변환 시도 금지). |

번호/이름(`7`, `07_bert_pipeline`)·`--all` 은 노트북 규약(`NN_slug/NN_slug.ipynb`)을 가리키므로 `.ipynb`
경로로 본다. 확장자가 불분명하면 사용자에게 확인한다. 새 포맷을 추가할 때는 이 표에 한 줄과 그
포맷용 파이프라인 절을 더하면 된다.

---

# 노트북(.ipynb) 파이프라인

노트북(.ipynb)을 변환할 때의 처리다. **핵심은 단순 파싱이 아니라 코드의 실제 실행 결과까지 싣는 것**이다.

**핵심 원칙**
- 코드를 실으면 그 코드의 **실제 실행 결과**도 함께 싣는다 — 가짜 출력을 지어내지 않는다.
  실행 결과가 없으면 코드만 싣고, 노트북 전체에 출력이 없을 땐 ①에서 사용자에게 실행 여부를 묻는다.
- 같은 `.md` 가 **웹(WikiDocs)·PDF·EPUB 세 타깃** 어디서도 깨지지 않게 한다(서점 판매엔 EPUB 필수).

**스크립트 위치(중요)**: 스킬 실행 시 작업 디렉터리는 **사용자 프로젝트**다(플러그인 폴더가 아님).
번들된 스크립트·설정은 플러그인 설치 경로를 가리키는 환경변수 `${CLAUDE_PLUGIN_ROOT}` 로 잡는다.
아래 명령에서 편의상 다음을 먼저 둔다(이 변수가 비어 있으면 이 SKILL.md 가 있는 폴더로 대체):
```bash
SK="${CLAUDE_PLUGIN_ROOT:-.}/skills/convert"   # scripts/ 가 이 아래
```

## 파이프라인

`① 실행 결과 확보 → ② 변환(build_wikidocs.py) → ③ 검증(check_wikidocs_md.py) → ④ 결과 해석 덧붙이기`

대상 프로젝트(노트북이 있는 곳)는 `--root` 로 지정한다(기본: 현재 디렉터리). 산출물(`pages/`,
`assets/`, `TOC.md`)도 `--root` 아래에 만들어진다. 실행본은 소스 노트북 옆 `<이름>_executed.ipynb`.

### ① 실행 결과 확보 — `<이름>_executed.ipynb`

변환기는 출력 원천을 이 순서로 자동 탐색한다(별도 `executed/` 폴더는 쓰지 않는다):
**`--executed-notebook` → 소스 옆 `<이름>_executed.ipynb` → 노트북 자체에 박힌 출력 → (없음)**.

**변환 전 반드시 확인 — 출력 원천이 하나도 없으면, 조용히 넘어가지 말고 사용자에게 물어본다(가짜 출력 금지):**

> "이 노트북에 실행 결과가 없습니다. ⓐ 실행해서 실제 출력까지 실을까요, 아니면 ⓑ 코드만 실을까요?"

- **ⓑ 코드만** → `--execute` 없이 변환한다(출력 없는 셀은 코드만).
- **ⓐ 실행 원함** → 노트북 성격에 맞는 경로로 실행하고, **결과를 소스 옆 `<이름>_executed.ipynb` 로 저장**한 뒤 ② 변환:
  - **CPU로 충분**(sklearn·간단 토크나이저 등) → `--execute --save-executed`. 임의의 절대/상대 경로에서도 동작.
    ```bash
    python3 "$SK/scripts/build_wikidocs.py" /abs/path/to/foo.ipynb --execute --save-executed
    # → /abs/path/to/foo_executed.ipynb 생성 후 변환
    ```
  - **GPU 필요**(BERT 학습·LLM 등) → **colab-cli** 로 Colab VM 에서 실행(아래). VM 이 저장소를 clone 해
    **이름으로** 노트북을 찾으므로, 대상 노트북은 **푸시된 git 저장소 안**에 있어야 한다(아니면 사용자에게 안내).

**colab-cli (GPU 실행, 권장)** — [`google-colab-cli`](https://github.com/googlecolab/google-colab-cli) 로
**터미널에서** VM 할당→실행→회수. 결과를 로컬 소스 옆에 받아 **PAT 불필요**, 인증 1회면 스킬이 직접 실행.
```bash
# 사전 1회 (issue #14 keep-alive 수정본 — PyPI v0.5.11 이하는 VM 이 ~11분에 idle-prune)
uv tool install "git+https://github.com/googlecolab/google-colab-cli"
colab --auth=oauth2 whoami       # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.

bash "$SK/scripts/run_via_cli.sh" --root <프로젝트> 7 24                  # 번호/폴더명/이름/.ipynb 경로 모두 가능
bash "$SK/scripts/run_via_cli.sh" --root <프로젝트> /abs/path/to/foo.ipynb
```
REPO 는 git origin 에서 자동 인식(VM 은 clone 만). 소스 옆 `<이름>_executed.ipynb` 가 로컬에 쌓인 뒤 ② 변환.
멱등·재개: 소스가 안 바뀐 노트북은 skip(`FORCE=1` 로 강제).

### ② 변환 — `scripts/build_wikidocs.py`

```bash
python3 "$SK/scripts/build_wikidocs.py" path/to/notebook.ipynb --root <프로젝트>
python3 "$SK/scripts/build_wikidocs.py" 7 24 --root <프로젝트>           # <이름>_executed.ipynb 자동 사용
python3 "$SK/scripts/build_wikidocs.py" --all --root <프로젝트>          # 전체 (사용자 확인 후)
```

**출력 원천 우선순위**(노트북별 자동): `--executed-notebook` > 소스 옆 `<이름>_executed.ipynb` > `--execute` > 노트북 자체 출력 > (없음).

**분할(장→절)** — 기본은 단순화돼 있다:
- `--split` **없음(기본)**: 노트북 1개 = 페이지 1개. 설정·의존성 없이 동작. (`--split single` 도 동일.)
- `--split` (값 없이) 또는 `--split sections`: 한 장을 여러 절로 나눈다. 값 없는 `--split` 은 sections 와 같다.
  - **config 없음(기본 권장)**: 노트북의 `## `(H2) 헤딩을 절 경계로 삼는 **구조적 분할**. 키워드 매핑
    불필요·언어 중립. H1/첫 H2 이전 내용은 개요 페이지(+자동 로드맵), 각 H2 → `<stem>-1.md`, `-2.md`,
    … 서브페이지. H2 가 없으면 절 분할이 안 되니(개요 1페이지만) `--split single` 을 권한다.
  - **config 의 `section_rules`(레거시)**: 직접 만든 설정 JSON 으로 H2 제목을 키워드로 고정 버킷
    (실습/해부/변형/정리)에 매핑. 일반 사용자는 보통 필요 없다.

  **스킬이 분할을 돕는 법(절 묶음 제안)**: sections 변환 전에 노트북을 한 번 훑어 H2 헤딩 목록을
  사용자에게 보여주고 "이대로 절을 나눌까요? 아니면 묶을까요?"를 확인한다. 그대로면 config 없이
  `--split sections` 로 바로 변환. 인접 H2 들을 한 절로 **묶고 싶다면**, 변환 전에 해당 마크다운 셀들의
  헤딩 수준을 조정(묶을 H2 들 중 첫 번째만 `## `로 두고 나머지는 `### ` 이하로)하거나, 임시 config 를
  만들어 `--config` 로 넘긴다 — 어느 쪽이든 사용자가 JSON 을 직접 작성할 필요는 없다.

**전자책 안전 출력**(자동) — [wikidocs 전자책 작성시 주의할 점](https://wikidocs.net/198723) 기준:
출력 스타일 `code`(웹·PDF·EPUB 모두 안전), 헤딩 위아래 빈 줄, 수평선 제거, 본문 H1→H2 강등,
각주 이름 유니크화, 윈도우 경로 인라인 코드화, ML 노이즈 필터(HF Hub·tqdm·생성 보일러플레이트),
EPUB 긴 산문 줄 트렁케이트(표는 보존). 트렁케이트 제외는 `--no-truncate <이름...>` 또는 config `no_truncate`.

산출물: `pages/<이름>.md`(+ `-{practice,anatomy,variation,wrapup}.md`), 그림 `assets/<이름>-outK.png`,
`TOC.md`(해당 항목 블록만 교체). 노트북별 실패는 격리되어 배치를 멈추지 않는다.

### ③ 검증 — `scripts/check_wikidocs_md.py`

```bash
python3 "$SK/scripts/check_wikidocs_md.py" --root <프로젝트>   # pages/*.md 전수 검사. 위반 시 종료코드 1
```

변환기가 자동 방어하지만, **회귀·수기 편집**을 잡는 독립 린터다(코드펜스 안 제외).
이어서 사람이 확인: 코드 셀에 `▶ 실행 결과`(출력 없는 셀이 맞는지),
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
