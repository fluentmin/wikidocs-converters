# samples — `/wikidocs:convert` 변환 예시

이 폴더는 `/wikidocs:convert` 가 지원하는 입력 포맷의 **샘플 입력**을 모아 둔 곳입니다.
플러그인을 처음 써 보는 분이 "무엇을 넣으면 무엇이 나오는지"를 바로 확인할 수 있고, 동시에
변환기의 회귀 확인용 자료로도 씁니다.

산출물(`pages/`, `assets/`, `TOC.md`)은 WikiDocs 책 저장소 규약대로 **저장소 루트**에 생성됩니다
(입력은 `samples/` 하위, 산출물은 루트).

## 입력(이 폴더)

| 파일 | 포맷 | 보여주는 것 |
|---|---|---|
| `01_pandas_intro.ipynb` | 노트북(.ipynb) | 코드 + **실제 실행 결과**(로그·표·그림). 출력이 임베드돼 있어 colab 없이 변환됩니다. |
| `02_markdown_guide.md` | 마크다운(.md) | 첫 H1 제거·각주 유니크화·**이미지 로컬화**(로컬 `diagram.png` → `assets/`). |
| `diagram.png` | 이미지 | `.md` 가 참조하는 로컬 이미지(로컬화 데모용). |

## 산출물 (저장소 루트에 생성)

| 파일/폴더 | 내용 |
|---|---|
| `../pages/*.md` | 변환된 WikiDocs 페이지(`--split` 이라 개요 + 절 서브페이지). |
| `../assets/*` | 추출·로컬화된 그림(노트북 그림 `-outK.png`, 마크다운 이미지 `-imgK.확장자`). |
| `../TOC.md` | 두 샘플이 번호 순으로 쌓인 목차. |

## 다시 만들기

저장소 루트에서 실행합니다(`--root .` = 루트에 `pages/`·`assets/`·`TOC.md` 생성, 입력은 `samples/`).

```bash
SK="plugins/wikidocs/skills/convert"
BT="WikiDocs 변환 샘플"

# 노트북(.ipynb) → 루트 pages·assets·TOC (H2 단위 분할)
python3 "$SK/scripts/build_wikidocs.py" samples/01_pandas_intro.ipynb --root . --split --book-title "$BT"

# 마크다운(.md) → 같은 산출물에 누적 (이미지 로컬화 포함)
python3 "$SK/scripts/convert_markdown.py" samples/02_markdown_guide.md --root . --split --book-title "$BT"

# 전자책 규칙 검증 (위반 시 종료코드 1)
python3 "$SK/scripts/check_wikidocs_md.py" --root .
```

> 실제 책 저장소에서 이 플러그인을 쓸 때도 동일합니다 — 변환할 파일을 repo 안에 두고 `--root` 를
> repo 루트로 주면, WikiDocs 가 동기화하는 `pages/`·`TOC.md` 가 루트에 만들어집니다.
> 새 포맷(docx·pdf·pptx 등)이 추가되면 같은 방식으로 입력 샘플을 이 폴더에 더합니다.
