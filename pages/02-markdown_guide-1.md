본문에는 H1(`#`)을 두지 않습니다 — 페이지 제목은 `TOC.md` 가 담당하므로, 변환기가 문서의
첫 H1 을 자동으로 떼어내고 그 뒤의 H1 은 H2 로 강등합니다[^02-markdown_guide-h1].

코드는 인라인 `pd.read_csv()` 처럼 쓰거나 펜스로 묶습니다.

```python
# 코드펜스 안의 ## 은 절 헤딩으로 보지 않습니다
print("hello, wikidocs")
```

[^02-markdown_guide-h1]: WikiDocs 전자책은 페이지 제목과 본문 H1 이 충돌하므로 본문에 H1 이 없어야 합니다.
