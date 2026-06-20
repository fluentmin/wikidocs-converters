# wikidocs-converters — Claude Code 플러그인 마켓플레이스

여러 포맷의 문서를 **WikiDocs용 마크다운**으로 변환하는 플러그인 모음입니다. 같은 `.md` 가
**웹(WikiDocs)·PDF·EPUB(전자책)** 어디서도 깨지지 않게 만드는 것이 공통 목표입니다.

- **현재 지원**: Jupyter 노트북(.ipynb) — 단순 파싱이 아니라 **코드의 실제 실행 결과(표·로그·그림)까지** 싣습니다.
- **향후 계획**: docx · pdf · pptx · md 등 포맷별 플러그인을 같은 마켓플레이스에 추가합니다.

## 설치

```
/plugin marketplace add fluentmin/wikidocs-converters
/plugin install notebook-to-wikidocs@wikidocs-converters
```

(아직 GitHub에 푸시 전이라면 로컬 경로로도 추가할 수 있습니다:
`/plugin marketplace add /path/to/wikidocs-converters`)

## 들어 있는 플러그인

### `notebook-to-wikidocs` — Jupyter 노트북 변환기

스킬 하나(`/notebook-to-wikidocs`)와 스크립트 4종:

| 파일 | 역할 |
|---|---|
| `scripts/build_wikidocs.py` | 노트북 → `pages/*.md` + `assets/` + `TOC.md` 변환(전자책 안전 출력) |
| `scripts/check_wikidocs_md.py` | 전자책 작성 규칙 린터(회귀·수기편집 점검) |
| `scripts/run_via_cli.sh` | google-colab-cli 로 실행본(`<이름>_executed.ipynb`) 자동 생성(macOS/Linux) |
| `scripts/colab_cli_exec.py` | 위 러너가 Colab VM 에서 돌리는 실행기 |
| `config/neuqes-101.json` | 장→절 분할(실습/해부/변형/정리) 설정 예시 — 복사해 자기 키워드로 |

**기본 동작은 노트북 1개 = 페이지 1개(`--split single`)** 로 의존성 없이 돕니다.
한 장을 여러 절로 쪼개려면 `--config` 로 키워드 매핑을 주고 `--split sections` 를 씁니다.

## 사용 예시 — 실제 노트북에 적용

설치 후 슬래시 명령으로 호출합니다(`disable-model-invocation` 이라 사용자가 직접 호출).
대상 노트북이 있는 폴더가 작업 디렉터리이거나 `--root` 로 지정합니다.

### 예시 1 — 노트북 1개 변환 (기본 single 모드)

```
/notebook-to-wikidocs analysis.ipynb
```

- 노트북에 **실행 출력이 이미 있으면** 그대로 싣고 변환합니다.
- 출력이 **없으면** 스킬이 먼저 물어봅니다 — "실행해서 결과까지 실을까요(ⓐ), 코드만(ⓑ)?"
  - ⓐ + CPU 노트북 → `--execute` (표준 라이브러리 실행, 추가 설치 불필요) → `analysis_executed.ipynb` 생성 후 변환
  - ⓐ + GPU 노트북 → colab-cli 로 Colab 에서 실행(아래 "사전 준비") → `analysis_executed.ipynb` 회수 후 변환
  - ⓑ → 출력 없는 셀은 코드만

생기는 파일:

```
프로젝트/
├─ analysis.ipynb                # 원본 (그대로)
├─ analysis_executed.ipynb       # ⓐ 실행을 택했을 때만 생성 (실행 출력 포함)
├─ pages/
│  └─ analysis.md                # ← 변환 결과 (코드 + 실제 실행 결과)
├─ assets/
│  └─ analysis-out1.png          # 노트북이 그린 그림들 (out1, out2, …)
└─ TOC.md                        # WikiDocs 목차 (이 페이지 항목 추가/갱신)
```

### 예시 2 — 한 노트북을 장→절 여러 페이지로 (sections 모드)

`07_bert_pipeline.ipynb` 같은 "한 챕터" 노트북을 실습/해부/변형/정리 절로 나눠 출판할 때:

```
/notebook-to-wikidocs 07_bert_pipeline --split sections --config config/neuqes-101.json
```

(`config/neuqes-101.json` 의 `section_rules`·`labels`·`book_title` 을 복사해 자기 키워드로 바꿔 쓰면 됩니다.)

생기는 파일:

```
프로젝트/
├─ pages/
│  ├─ 07-bert_pipeline.md             # 장 개요 + "이 장의 구성" 로드맵
│  ├─ 07-bert_pipeline-practice.md    # 실습 절
│  ├─ 07-bert_pipeline-anatomy.md     # 해부 절
│  ├─ 07-bert_pipeline-variation.md   # 변형 절
│  └─ 07-bert_pipeline-wrapup.md      # 정리·FAQ 절
├─ assets/
│  └─ 07-bert_pipeline-out1.png       # 그림 (상대경로 ../assets/ 로 참조)
└─ TOC.md                             # 해당 장 블록만 교체(다른 장 보존)
```

### 예시 3 — 여러 노트북 일괄 변환

```
/notebook-to-wikidocs 1 7 24            # 번호로 (NN_slug/NN_slug.ipynb 규약)
/notebook-to-wikidocs --all             # --root 아래 모든 노트북 (사용자 확인 후)
```

각 노트북은 위와 같은 `pages/*.md` + `assets/*.png` 를 만들고, `TOC.md` 에 항목이 번호 순서로 쌓입니다.

### 변환 후 검증 (선택)

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/notebook-to-wikidocs/scripts/check_wikidocs_md.py" --root .
```

전자책 작성 규칙(본문 H1 금지, 헤딩 빈 줄, 외부 이미지·raw HTML·수평선 금지, 각주 유니크 등)을
`pages/*.md` 전수 검사합니다. 위반이 있으면 종료코드 1.

> 산출물(`pages/`·`assets/`·`TOC.md`)을 WikiDocs 에 올리는 방법: `pages/*.md` 내용을 각 페이지에 붙여넣고,
> `assets/*.png` 는 WikiDocs 에 업로드(외부 이미지 링크는 전자책에서 깨지므로), `TOC.md` 순서대로 목차를 구성합니다.

## 사전 준비 (colab-cli 경로를 쓸 때만)

```bash
uv tool install "git+https://github.com/googlecolab/google-colab-cli"   # issue #14 keep-alive 수정본
colab --auth=oauth2 whoami        # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.
```

자세한 사용법은 플러그인 스킬 문서(`SKILL.md`) 참고.
