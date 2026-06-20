# wikidocs-converters — Claude Code 플러그인 마켓플레이스

여러 포맷의 문서를 **WikiDocs용 마크다운**으로 변환합니다. `/wikidocs:convert <파일>` 하나로
**파일 확장자를 보고 알맞은 변환기를 자동 선택**합니다(같은 `.md` 가 웹·PDF·EPUB 어디서도 깨지지 않게).
## **사람이 해야하는** 최초 작업(GitHub과 WikiDocs 연동)
먼저 WikiDocs에 회원가입 및 로그인한 후 https://wikidocs.net/profile/edit/book 에서 [새 책 만들기 (깃허브 연동)](https://wikidocs.net/321336)를 참고하여 깃허브와 연동된 WikiDocs 책을 만들어주세요. 책을 만들고 나면 `README.md`, `TOC.md` 중 기존 GitHub repo에 없는 파일이 push됩니다. 이 플러그인을 사용하여 변환할 파일은 해당 repo 하위에 위치시켜주세요.

- **현재 지원**: Jupyter 노트북(.ipynb) — 단순 파싱이 아니라 **코드의 실제 실행 결과(표·로그·그림)까지** 싣습니다.
- **향후 계획**: docx · pdf · pptx · md 등 포맷을 **같은 `/wikidocs:convert` 스킬**에 추가합니다(확장자로 분기).

## 플러그인 설치

```bash
cd <위에서 WikiDocs와 연동해둔 GitHub repo 경로>
claude # Claude Code CLI에서 아래 명령어 실행
/plugin marketplace add fluentmin/wikidocs-converters
/plugin install wikidocs@wikidocs-converters
/reload-plugins
```

## 들어 있는 플러그인

### `wikidocs` — 문서 → WikiDocs 변환기

스킬 하나(`/wikidocs:convert`)가 파일 확장자로 변환기를 고릅니다. 현재 `.ipynb` 경로(스크립트 4종):

| 파일 | 역할 |
|---|---|
| `scripts/build_wikidocs.py` | 노트북 → `pages/*.md` + `assets/` + `TOC.md` 변환(전자책 안전 출력) |
| `scripts/check_wikidocs_md.py` | 전자책 작성 규칙 린터(회귀·수기편집 점검) |
| `scripts/run_via_cli.sh` | google-colab-cli 로 실행본(`<이름>_executed.ipynb`) 자동 생성(macOS/Linux) |
| `scripts/colab_cli_exec.py` | 위 러너가 Colab VM 에서 돌리는 실행기 |

**기본 동작은 노트북 1개 = 페이지 1개** 로 의존성 없이 돕니다.
한 장을 여러 절로 쪼개려면 **`--split` 플래그**만 붙이면 됩니다(값 없이 `--split` = `--split sections`) —
**노트북의 `## `(H2) 헤딩을 절 경계로 자동 분할**합니다(아래 예시 2).

## 사용 예시 — 실제 노트북에 적용

설치 후 `/wikidocs:convert <파일>` 로 호출합니다(`disable-model-invocation` 이라 사용자가 직접 호출).
스킬이 확장자를 보고 변환기를 고릅니다 — 아래 예시는 모두 `.ipynb`(현재 지원 포맷)입니다.
대상 노트북이 있는 폴더가 작업 디렉터리이거나 `--root` 로 지정합니다.

### 예시 1 — 노트북 1개 변환 (기본 single 모드)

```
/wikidocs:convert analysis.ipynb
```

- **경로는 절대경로·상대경로 모두 가능**합니다.
  - 절대경로: `/Users/me/proj/analysis.ipynb` 처럼 그대로 해석합니다.
  - 상대경로: `--root` 기준으로 해석합니다. `--root` 를 주지 않으면 기본값이 `.`,
    즉 **`claude` 를 실행한 현재 작업 디렉터리**(보통 `cd` 해 둔 GitHub repo 루트)가 기준입니다.
    예) repo 루트에서 실행했다면 `analysis.ipynb` → `<repo>/analysis.ipynb`,
    `notebooks/analysis.ipynb` → `<repo>/notebooks/analysis.ipynb`.
  - `--root ~/proj` 처럼 루트를 따로 지정하면 상대경로는 그 폴더 기준이 됩니다
    (예: `--root ~/proj analysis.ipynb` → `~/proj/analysis.ipynb`).
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

내용이 긴 "한 챕터" 노트북을 여러 절로 나눠 출판할 때

```
/wikidocs:convert report.ipynb --split
```

- `--split` 만 붙이면 분할이 켜집니다(값 없이 `--split` = `--split sections`).
- 분할 기준은 **노트북 안의 `## `(H2) 헤딩**입니다. 
  노트북에 적어둔 헤딩 구조를 그대로 따릅니다(한글·영문 무관).
  - H1 제목과 첫 H2 이전 내용 → **개요 페이지**(`report.md`) + 자동 생성된 "이 장의 구성" 로드맵
  - 각 `## ` 헤딩 → 문서 순서대로 `report-1.md`, `report-2.md`, … 서브페이지(절 제목 = 헤딩 텍스트)
- 노트북에 `## ` 헤딩이 없으면 절로 나눌 게 없어 개요 1페이지만 생깁니다 → 이때는 `--split` 을 빼고 실행하세요.

> 스킬로 호출하면, 변환 전에 Claude 가 노트북의 H2 목록을 보여주고 "이대로 나눌까요, 몇 개를 한 절로
> 묶을까요?"를 먼저 확인합니다. JSON 을 직접 작성할 필요는 없습니다.

생기는 파일(노트북에 `## ` 헤딩이 3개일 때):

```
프로젝트/
├─ report.ipynb              # 원본 (그대로)
├─ pages/
│  ├─ report.md              # 장 개요 + "이 장의 구성" 로드맵
│  ├─ report-1.md            # 첫 번째 ## 절 (제목 = 그 헤딩 텍스트)
│  ├─ report-2.md            # 두 번째 ## 절
│  └─ report-3.md            # 세 번째 ## 절
├─ assets/
│  └─ report-out1.png        # 그림 (상대경로 ../assets/ 로 참조)
└─ TOC.md                    # 개요 + 각 절 항목 추가/갱신
```

### 예시 3 — 여러 노트북 일괄 변환

```
/wikidocs:convert 1 7 24            # 번호로 (NN_slug/NN_slug.ipynb 규약)
/wikidocs:convert --all             # --root 아래 모든 노트북 (사용자 확인 후)
```

각 노트북은 위와 같은 `pages/*.md` + `assets/*.png` 를 만들고, `TOC.md` 에 항목이 번호 순서로 쌓입니다.

### 변환 후 검증 (선택)

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/convert/scripts/check_wikidocs_md.py" --root .
```

전자책 작성 규칙(본문 H1 금지, 헤딩 빈 줄, 외부 이미지·raw HTML·수평선 금지, 각주 유니크 등)을
`pages/*.md` 전수 검사합니다. 위반이 있으면 종료코드 1.

> **노트북에 H1(`# `)이 여러 번 나오면**: WikiDocs 전자책은 페이지 제목과 충돌하므로 **본문에 H1 이
> 없어야** 합니다. 변환기는 **맨 처음 H1 하나만 페이지 제목**으로 쓰고 본문에서 떼어내며, **두 번째
> 이후의 H1 은 자동으로 H2(`## `)로 강등**해 규칙 위반을 막습니다(변환 로그에 `H1→H2` 건수로 표시).
> sections 모드에서도 두 번째 이후 H1 은 새 절을 만들지 않고 본문 H2 로 들어갑니다 — 절을 나누려면 `## ` 를 쓰세요.

> 산출물(`pages/`·`assets/`·`TOC.md`)을 WikiDocs 에 올리는 방법: `pages/*.md` 내용을 각 페이지에 붙여넣고,
> `assets/*.png` 는 WikiDocs 에 업로드(외부 이미지 링크는 전자책에서 깨지므로), `TOC.md` 순서대로 목차를 구성합니다.

## 사전 준비 (colab-cli 경로를 쓸 때만)

```bash
uv tool install "git+https://github.com/googlecolab/google-colab-cli"   # issue #14 keep-alive 수정본
colab --auth=oauth2 whoami        # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.
```

자세한 사용법은 플러그인 스킬 문서(`SKILL.md`) 참고.
