"""뽐뿌 해외뽐뿌(ppomppu8) 게시판에서 GMKtec K12 (알림) 와 FIREBAT F1 7640HS·H255 (기록만) 가격을 감시한다.

- 게시판 목록의 모든 글을 열어 제목 + 본문을 함께 검사한다.
- 감시 상품 글이면 가격을 뽑아 data/price_history.csv 에 기록한다 (가격 모니터링).
- K12 가격이 기준가(기본 $200) 미만이면 GitHub 이슈로 알린다 (선택: 텔레그램).

외부 의존성 없이 표준 라이브러리만 사용한다.
"""

import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BOARD_ID = "ppomppu8"
BASE = "https://m.ppomppu.co.kr/new/"
LIST_URL = BASE + "bbs_list.php?id={board}&page={page}"
VIEW_URL = BASE + "bbs_view.php?id={board}&no={no}"

THRESHOLD_USD = float(os.environ.get("THRESHOLD_USD", "200"))
KRW_PER_USD = float(os.environ.get("KRW_PER_USD", "1400"))
PAGES = int(os.environ.get("PAGES", "2"))
# 쿠폰 할인액("$30 할인") 같은 숫자를 가격으로 오인하지 않도록 하한을 둔다.
MIN_PLAUSIBLE_USD = float(os.environ.get("MIN_PLAUSIBLE_USD", "80"))
HISTORY_FILE = os.environ.get("HISTORY_FILE", "data/price_history.csv")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- fetching


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": BASE + "bbs_list.php?id=" + BOARD_ID,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset()
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000], re.I)
        charset = m.group(1).decode() if m else "cp949"
    if charset.lower() in ("euc-kr", "ks_c_5601-1987"):
        charset = "cp949"  # euc-kr 의 상위집합
    return raw.decode(charset, errors="replace")


def list_post_ids(page_html):
    ids = []
    for m in re.finditer(r'href="([^"]*bbs_view\.php\?[^"]*)"', page_html):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(m.group(1))).query)
        if q.get("id", [None])[0] == BOARD_ID and q.get("no"):
            no = q["no"][0]
            if no.isdigit() and no not in ids:
                ids.append(no)
    return ids


# ---------------------------------------------------------------- parsing


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def extract_title(page_html):
    for pat in (
        r'(?is)<meta\s+property="og:title"\s+content="([^"]*)"',
        r"(?is)<h4[^>]*>(.*?)</h4>",
        r"(?is)<title>(.*?)</title>",
    ):
        m = re.search(pat, page_html)
        if m:
            t = html_to_text(m.group(1))
            if t:
                return t
    return ""


def extract_body(page_html):
    """본문 영역만 뽑는다. 사이드바의 다른 글 제목에 속지 않도록 가능한 좁게 잡는다."""
    for pat in (
        r'(?is)<div[^>]+class="[^"]*\bcont\b[^"]*"[^>]*>(.*?)<div[^>]+class="[^"]*(?:cmAr|comment|reply)',
        r'(?is)<td[^>]+class="[^"]*board-contents[^"]*"[^>]*>(.*?)</td>',
        r'(?is)<div[^>]+id="KH_Content"[^>]*>(.*?)</div>',
        r'(?is)<div[^>]+class="[^"]*\bcont\b[^"]*"[^>]*>(.*?)</div>\s*</div>',
    ):
        m = re.search(pat, page_html)
        if m and len(html_to_text(m.group(1))) > 20:
            return html_to_text(m.group(1))
    return html_to_text(page_html)


def _rx(p):
    return re.compile(p, re.I)


# 감시 대상 상품. match 의 정규식이 모두 (제목+본문 기준) 나와야 해당 상품 글로 본다.
# alert=True 인 상품만 기준가 미만일 때 알림, 나머지는 가격 기록만 한다.
PRODUCTS = [
    {
        "key": "gmktec-k12",
        "name": "GMKtec K12",
        "anchor": _rx(r"(?<![a-z0-9])k[\s-]?12(?![0-9])"),
        "match": [_rx(r"gmk\s?tec|gmk(?![a-z])|지엠케이|nucbox|누크박스|미니\s?pc|mini\s?pc")],
        "alert": True,
    },
    {
        "key": "firebat-f1-7640hs",
        "name": "FIREBAT F1 7640HS",
        "anchor": _rx(r"7640\s?hs"),
        "match": [_rx(r"firebat|파이어\s?뱃"), _rx(r"(?<![a-z0-9])f1(?![0-9])")],
        "alert": False,
    },
    {
        "key": "firebat-f1-h255",
        "name": "FIREBAT F1 H255",
        "anchor": _rx(r"(?<![a-z0-9])h\s?255(?![0-9])"),
        "match": [_rx(r"firebat|파이어\s?뱃"), _rx(r"(?<![a-z0-9])f1(?![0-9])")],
        "alert": False,
    },
]

NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
USD_PATTERNS = [
    re.compile(r"(?:\$|US\s?\$|USD)\s?" + NUM, re.I),
    re.compile(NUM + r"\s?(?:\$|달러|불|usd)", re.I),
]
KRW_PATTERNS = [
    re.compile(r"(\d{1,3}(?:,\d{3})+|\d{5,7})\s?원"),
    re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s?만\s?원"),
    re.compile(r"₩\s?(\d{1,3}(?:,\d{3})+|\d{5,7})"),
]
# "할인가 $254.32", "최저가: $304.91" → 최종 가격
FINAL_LABEL = _rx(r"(할인가|최저가|최종가|실구매가|실결제가|결제가|구매가|최종|적용가|막차가)\s*[:：]?\s*[^\n\d$]{0,6}$")
# "카드할인 $100", "할인코드 $36", "결제 할인 $100" → 할인 금액이지 가격이 아님
DISCOUNT_LABEL = _rx(r"(할인|쿠폰|코드|코인|적립|캐시백|페이백|coupon|off)[^\n]{0,4}$")
DISCOUNT_SUFFIX = _rx(r"^\s*(할인|off|쿠폰|적립|캐시백)")


def _num(whole, frac=None):
    v = float(whole.replace(",", ""))
    if frac:
        v += float("0." + frac)
    return v


def find_prices(text):
    """[(usd, 원문표기, 위치, 종류)] — 종류: final / plain. 할인 금액은 제외. 원화는 환산."""
    raw = []
    for p in USD_PATTERNS:
        for m in p.finditer(text):
            raw.append((_num(m.group(1), m.group(2)), m))
    for i, p in enumerate(KRW_PATTERNS):
        for m in p.finditer(text):
            krw = float(m.group(1)) * 10000 if i == 1 else _num(m.group(1))
            raw.append((round(krw / KRW_PER_USD, 2), m))
    out, seen = [], set()
    for v, m in sorted(raw, key=lambda x: x[1].start()):
        if m.start() in seen or not (MIN_PLAUSIBLE_USD <= v < 5000):
            continue
        seen.add(m.start())
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[max(line_start, m.start() - 15): m.start()]
        if FINAL_LABEL.search(prefix):
            kind = "final"
        elif DISCOUNT_LABEL.search(prefix) or DISCOUNT_SUFFIX.search(text[m.end(): m.end() + 8]):
            continue
        else:
            kind = "plain"
        out.append((v, m.group(0).strip(), m.start(), kind))
    return out


def pick_price(prices):
    """'할인가/최저가' 로 표시된 가격이 있으면 그중 최저, 없으면 전체 최저."""
    finals = [p for p in prices if p[3] == "final"]
    return min(finals or prices, key=lambda p: p[0]) if prices else None


def product_segments(text, product):
    """본문에서 해당 상품 이름 뒤 ~ 다른 상품 이름 전까지 구간 (여러 상품 모음글 대비)."""
    others = [p["anchor"] for p in PRODUCTS if p is not product]
    segs = []
    for m in product["anchor"].finditer(text):
        end = min(len(text), m.end() + 800)
        for o in others:
            om = o.search(text, m.end(), end)
            if om:
                end = min(end, om.start())
        segs.append((m.start(), end))
    return segs


def analyze(title, body, product):
    """해당 상품 글이면 dict 반환, 아니면 None."""
    full = title + "\n" + body
    if not product["anchor"].search(full):
        return None
    if not all(r.search(full) for r in product["match"]):
        return None

    in_title = bool(product["anchor"].search(title))
    # 1순위: 제목 가격 (뽐뿌 제목은 보통 "(가격/배송비)" 형식). 제목에 다른 상품도 있으면 건너뜀.
    title_has_other = any(p["anchor"].search(title) for p in PRODUCTS if p is not product)
    best, source, src = None, "none", ""
    if in_title and not title_has_other:
        best, source, src = pick_price(find_prices(title)), "title", title
    if not best:
        # 2순위: 본문에서 상품 이름 근처 구간
        prices = [p for lo, hi in product_segments(body, product)
                  for p in find_prices(body[lo:hi])
                  for p in [(p[0], p[1], p[2] + lo, p[3])]]
        if not prices and in_title and not title_has_other:
            prices = find_prices(body)  # 단일 상품 글: 본문 전체
        best, source, src = pick_price(prices), "body", body
    if not best:
        return {"price": None, "price_text": "", "source": "none", "context": ""}
    ctx = src[max(0, best[2] - 120): best[2] + 120].replace("\n", " ")
    return {"price": best[0], "price_text": best[1], "source": source, "context": ctx}


# ---------------------------------------------------------------- outputs


def append_history(rows):
    """새 글이거나 가격이 바뀐 경우만 기록 → 가격 추이 확인용."""
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    last = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                last[(r.get("product", "gmktec-k12"), r["post_no"])] = r["price_usd"]
    new = [r for r in rows if last.get((r["product"], r["post_no"])) != r["price_usd"]]
    if not new:
        return []
    write_header = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["checked_at_kst", "product", "post_no", "price_usd", "price_text", "title", "url"])
        if write_header:
            w.writeheader()
        w.writerows(new)
    return new


def github_api(method, path, data=None):
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or "null")


def already_alerted(no):
    issues = github_api("GET", "/issues?state=all&labels=price-alert&per_page=100")
    return any(f"#{no}]" in i["title"] for i in issues)


def notify(hit):
    title = f"[K12 ${hit['price']:.2f}] {hit['title'][:80]} [뽐뿌#{hit['post_no']}]"
    body = (
        f"GMKtec K12 가격이 **${hit['price']:.2f}** 로 기준가 ${THRESHOLD_USD:.0f} 미만입니다.\n\n"
        f"- 글: {hit['url']}\n"
        f"- 제목: {hit['title']}\n"
        f"- 가격 표기: `{hit['price_text']}` ({'제목' if hit['source'] == 'title' else '본문'}에서 추출)\n"
        f"- 원화 환산 기준: 1 USD = {KRW_PER_USD:.0f} KRW\n\n"
        f"> …{hit['context']}…\n\n"
        "자동 추출이라 오인식일 수 있으니 글을 직접 확인해 주세요."
    )
    if DRY_RUN or not os.environ.get("GITHUB_TOKEN"):
        print("[DRY] would alert:", title)
        return
    if already_alerted(hit["post_no"]):
        print("already alerted:", hit["post_no"])
        return
    github_api("POST", "/issues", {"title": title, "body": body, "labels": ["price-alert"]})
    print("ALERT issue created:", title)

    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        msg = f"GMKtec K12 ${hit['price']:.2f}\n{hit['title']}\n{hit['url']}"
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": tg_chat, "text": msg}).encode(),
            timeout=30,
        )


# ---------------------------------------------------------------- main


def main():
    ids = []
    for page in range(1, PAGES + 1):
        page_html = fetch(LIST_URL.format(board=BOARD_ID, page=page))
        found = list_post_ids(page_html)
        print(f"list page {page}: {len(found)} posts")
        ids += [i for i in found if i not in ids]
    if not ids:
        print("ERROR: 목록에서 글을 하나도 찾지 못했습니다 (차단 또는 페이지 구조 변경).")
        return 1

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    rows, hits = [], []
    for no in ids:
        url = VIEW_URL.format(board=BOARD_ID, no=no)
        try:
            page_html = fetch(url)
        except Exception as e:  # 한 글 실패로 전체를 멈추지 않는다
            print(f"skip {no}: {e}")
            continue
        time.sleep(1)
        title, body = extract_title(page_html), extract_body(page_html)
        for product in PRODUCTS:
            res = analyze(title, body, product)
            if not res:
                continue
            print(f"{product['name']} post {no}: price={res['price']} ({res['price_text']}) {title}")
            price = "" if res["price"] is None else f"{res['price']:.2f}"
            rows.append({"checked_at_kst": now, "product": product["key"], "post_no": no,
                         "price_usd": price, "price_text": res["price_text"], "title": title, "url": url})
            if product["alert"] and res["price"] is not None and res["price"] < THRESHOLD_USD:
                hits.append({**res, "post_no": no, "title": title, "url": url})

    print(f"checked {len(ids)} posts, product posts: {len(rows)}, K12 under ${THRESHOLD_USD:.0f}: {len(hits)}")
    for r in append_history(rows):
        print("history +", r["post_no"], r["price_usd"])
    for h in hits:
        notify(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
