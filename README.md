# 뽐뿌 미니PC 가격 감시

뽐뿌 해외뽐뿌 게시판(`ppomppu8`)을 30분마다 확인합니다 (GitHub Actions).

| 상품 | 동작 |
| --- | --- |
| GMKtec K12 | **$200 미만**이면 `price-alert` 이슈로 알림 + 가격 기록 |
| FIREBAT F1 7640HS | 가격 기록만 |
| FIREBAT F1 H255 | 가격 기록만 |

## 동작 방식
- GitHub 서버(해외 IP)에서는 뽐뿌 게시판·글 페이지가 403 으로 막혀 있어, 뽐뿌 **통합검색(제목+내용)** 을 사용합니다.
  검색이 본문까지 보기 때문에 본문에만 상품명이 적힌 글도 찾습니다.
- 가격은 제목의 `상품명($가격)` 표기를 우선, 없으면 검색결과에 보이는 본문 앞부분에서 뽑습니다.
  `카드할인 $100`, `할인코드 $36` 같은 할인액은 가격으로 보지 않고 `할인가`/`최저가` 표기를 우선합니다.
- 본문 뒤쪽에만 가격이 있으면 가격을 알 수 없어 빈 값으로 기록됩니다 (전체 본문은 해외 IP 에서 열 수 없음).
  K12 글이면 **"K12 가격 확인 필요"** 알림을 보내니 링크를 열어 직접 확인하세요.
- 원화 표기는 1 USD = 1400원으로 환산합니다 (`KRW_PER_USD`).
- 기록: `data/price_history.csv` (게시일, 상품, 가격, 글 링크)
- 텔레그램 알림도 받으려면 저장소 Secrets 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 추가.

## 과거 가격 수집
Actions → *Price history backfill* → Run workflow (기본: 2026-01-01 이후).

테스트: `python -m unittest discover -s tests`

## 시험 알림
Actions → *GMKtec K12 price monitor* → Run workflow → **시험 알림만 보내기** 체크 → Run.
`[테스트 알림]` 이슈가 만들어지고, 알림(앱 푸시/메일)이 오는지 확인할 수 있습니다.
