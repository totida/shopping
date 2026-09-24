"""게시판 검색(제목+내용)으로 올해 초부터의 감시 상품 글을 모아 가격 추이를 만든다.

결과: data/backfill.csv (게시일, 상품, 가격, 제목, 링크)
"""

import csv
import html
import os
import re
import sys
import time
import urllib.parse

import monitor

SINCE = os.environ.get("SINCE", "2026-01-01")
MAX_PAGES = int(os.environ.get("MAX_PAGES", "15"))
OUT = os.environ.get("BACKFILL_FILE", "data/backfill.csv")
DEBUG = os.environ.get("DEBUG") == "1"

# (검색어, 제목+내용 검색). 한글 검색어는 cp949 로 인코딩해야 뽐뿌가 인식한다.
KEYWORDS = ["K12", "GMKtec", "FIREBAT", "파이어뱃"]
SEARCH_URL = monitor.BASE + "bbs_list.php?id={board}&page={page}&search_type=sub_memo&keyword={kw}"


def post_date(page_html):
    """글 작성일 (YYYY-MM-DD). 뽐뿌 모바일은 '2026-03-05 12:34' 또는 '26-03-05 12:34' 형태."""
    text = monitor.html_to_text(page_html)
    m = re.search(r"(20\d\d)[-./](\d\d)[-./](\d\d)\s+\d\d:\d\d", text)
    if m:
        return "-".join(m.groups())
    m = re.search(r"(?<!\d)(\d\d)[-./](\d\d)[-./](\d\d)\s+\d\d:\d\d", text)
    if m:
        return "20" + "-".join(m.groups())
    return ""


def main():
    seen, rows = set(), []
    for kw in KEYWORDS:
        q = urllib.parse.quote(kw, encoding="cp949")
        for page in range(1, MAX_PAGES + 1):
            page_html = monitor.fetch(SEARCH_URL.format(board=monitor.BOARD_ID, page=page, kw=q))
            ids = monitor.list_post_ids(page_html)
            if DEBUG and page == 1:
                print(f"--- search '{kw}' page 1 html sample ---\n{page_html[:3000]}\n---")
            print(f"search '{kw}' page {page}: {len(ids)} posts")
            if not ids:
                break
            oldest = "9999"
            for no in ids:
                if no in seen:
                    continue
                seen.add(no)
                url = monitor.VIEW_URL.format(board=monitor.BOARD_ID, no=no)
                try:
                    view = monitor.fetch(url)
                except Exception as e:
                    print(f"skip {no}: {e}")
                    continue
                time.sleep(1)
                date = post_date(view)
                if DEBUG and len(seen) == 1:
                    print(f"--- view {no} text sample ---\n{monitor.html_to_text(view)[:2000]}\n---")
                if date:
                    oldest = min(oldest, date)
                if date and date < SINCE:
                    continue
                title, body = monitor.extract_title(view), monitor.extract_body(view)
                for product in monitor.PRODUCTS:
                    res = monitor.analyze(title, body, product)
                    if res:
                        print(f"{date} {product['name']} {res['price']} {title}")
                        rows.append({
                            "posted_at": date, "product": product["key"], "post_no": no,
                            "price_usd": "" if res["price"] is None else f"{res['price']:.2f}",
                            "price_text": res["price_text"], "title": html.unescape(title), "url": url,
                        })
            if oldest < SINCE:
                break  # 검색 결과는 최신순이므로 기준일 이전이 나오면 중단

    rows.sort(key=lambda r: (r["product"], r["posted_at"], r["post_no"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["posted_at", "product", "post_no", "price_usd",
                                          "price_text", "title", "url"])
        w.writeheader()
        w.writerows(rows)
    print(f"saved {len(rows)} rows to {OUT}")
    return 0 if seen else 1


if __name__ == "__main__":
    sys.exit(main())
