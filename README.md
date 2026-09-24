# GMKtec K12 가격 감시

뽐뿌 해외뽐뿌 게시판(`ppomppu8`)을 30분마다 확인합니다 (GitHub Actions).

- 목록 1~2페이지의 **모든 글을 열어 제목과 본문을 함께** 검사합니다.
- K12 관련 글이면 가격을 `data/price_history.csv` 에 기록합니다 (가격 추이).
- 가격이 **$200 미만**이면 `price-alert` 라벨로 이슈를 만듭니다 → GitHub 알림/메일로 전달됩니다.
- 원화 표기는 1 USD = 1400원으로 환산합니다 (`KRW_PER_USD` 로 변경).
- 텔레그램 알림도 받고 싶다면 저장소 Secrets 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 추가하세요.

수동 실행: Actions 탭 → *GMKtec K12 price monitor* → Run workflow.
테스트: `python -m unittest discover -s tests`
