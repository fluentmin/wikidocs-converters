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

## 사전 준비 (colab-cli 경로를 쓸 때만)

```bash
uv tool install "git+https://github.com/googlecolab/google-colab-cli"   # issue #14 keep-alive 수정본
colab --auth=oauth2 whoami        # 동의 화면 "모두 선택". 과금 방지로 결제수단 없는 무료 계정 권장.
```

자세한 사용법은 플러그인 스킬 문서(`SKILL.md`) 참고.
