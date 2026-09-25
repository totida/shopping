"""뽐뿌 해외뽐뿌(ppomppu8) 에서 GMKtec K12 (알림) 와 FIREBAT F1 7640HS·H255 (기록만) 가격을 감시한다.

해외(GitHub 서버) IP 에서는 게시판·글 페이지가 403 으로 막혀 있어 뽐뿌 통합검색을 쓴다.
- 통합검색(제목+내용)으로 상품명이 언급된 글을 찾는다 → 본문에만 적힌 경우도 잡힌다.
- 가격은 제목의 "상품명($가격)" 표기를 우선, 없으면 검색결과에 보이는 본문 앞부분에서 뽑는다.
- 결과는 data/price_history.csv 에 게시일과 함께 기록한다 (가격 추이).
- K12 가격이 기준가(기본 $200) 미만이면 GitHub 이슈로 알린다 (선택: 텔레그램).
- K12 언급 글인데 가격을 못 읽었으면 '가격 확인 필요' 알림을 보낸다.

`python monitor.py` 는 최근 글만, `SINCE=2026-01-01 python monitor.py` 는 그 날짜까지 거슬러 수집한다.
외부 의존성 없이 표준 라이브러리만 사용한다.
"""

import csv
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BOARD_ID = "ppomppu8"
SEARCH_URL = ("https://www.ppomppu.co.kr/search_bbs.php?search_type={stype}&page_no={page}"
              "&keyword={kw}&bbs_id={board}&order_type={order}")
# 통합검색은 검색어 하나당 최근 45개 정도(5개 x 9페이지)만 보여준다.
# 과거 글 수집 시에는 '제목만'(결과가 적어 더 과거까지 닿음)과 '정확도순'을 섞어 범위를 넓힌다.
SEARCH_MODES_RECENT = [("sub_memo", "date")]
SEARCH_MODES_BACKFILL = [("sub_memo", "date"), ("subject", "date"), ("sub_memo", "relevance"), ("subject", "relevance")]
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "3"))
VIEW_URL = "https://www.ppomppu.co.kr/zboard/view.php?id={board}&no={no}"

THRESHOLD_USD = float(os.environ.get("THRESHOLD_USD", "200"))
KRW_PER_USD = float(os.environ.get("KRW_PER_USD", "1400"))
# 비어 있으면 검색 1페이지(최근 50개)만, 날짜를 주면 그 날짜 이전 글이 나올 때까지 페이지를 넘긴다.
SINCE = os.environ.get("SINCE", "")
MAX_PAGES = int(os.environ.get("MAX_PAGES", "40"))
# 쿠폰 할인액("$30 할인") 같은 숫자를 가격으로 오인하지 않도록 하한을 둔다.
MIN_PLAUSIBLE_USD = float(os.environ.get("MIN_PLAUSIBLE_USD", "80"))
HISTORY_FILE = os.environ.get("HISTORY_FILE", "data/price_history.csv")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
# 이 일수 이내에 올라온 글만 알림 (첫 실행 때 지난 글 알림이 쏟아지지 않도록)
ALERT_DAYS = int(os.environ.get("ALERT_DAYS", "2"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ---------------------------------------------------------------- fetching

_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    try:
        with _opener.open(req, timeout=30) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.url}\n{e.read()[:300].decode('cp949', errors='replace')}")
        raise
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000], re.I)
        charset = m.group(1).decode() if m else "cp949"
    if charset.lower() in ("euc-kr", "ks_c_5601-1987"):
        charset = "cp949"  # euc-kr 의 상위집합
    return raw.decode(charset, errors="replace")


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def parse_search(page_html):
    """통합검색 결과 → [{no, title, body(본문 앞부분), date, url}]"""
    out = []
    for block in page_html.split('<div class="conts">')[1:]:
        m = re.search(r"view\.php\?id=" + BOARD_ID + r"&(?:amp;)?no=(\d+)", block)
        if not m:
            continue
        no = m.group(1)
        t = re.search(r'(?is)<span class="title">\s*<a[^>]*>(.*?)(?:<font class=.comment-cnt|</a>)', block)
        b = re.search(r'(?is)<p style="height:\s*45px">\s*<a[^>]*>(.*?)</a>', block)
        d = re.search(r"<span>\s*(20\d\d)\.(\d\d)\.(\d\d)\s*</span>", block)
        out.append({
            "no": no,
            "title": html_to_text(t.group(1)) if t else "",
            "body": html_to_text(b.group(1)) if b else "",
            "date": "-".join(d.groups()) if d else "",
            "url": VIEW_URL.format(board=BOARD_ID, no=no),
        })
    return out


# ---------------------------------------------------------------- products


def _rx(p):
    return re.compile(p, re.I)


FIREBAT_RE = _rx(r"firebat|파이어\s?뱃")
# 세부 모델 표기: 7640HS 는 Ryzen 5, H255 는 Ryzen 7
V7640 = r"(?:7640\s?hs|ryzen\s?5|r5(?![0-9]))"
VH255 = r"(?:h\s?255(?![0-9])|ryzen\s?7|r7(?![0-9]))"
F1 = r"(?<![a-z0-9])f1(?![0-9])"
# 모델 표기가 붙지 않은 "FIREBAT F1" (뒤에 7640HS/H255 등이 오면 제외)
F1_RE = _rx(r"(?:firebat|파이어\s?뱃)\s*f1(?![0-9])(?![^$()\n,/]{0,40}?(?:" + V7640 + "|" + VH255 + "))")


# 감시 대상 상품.
#   anchor : 가격 위치를 찾는 기준이 되는 상품명
#   search : 통합검색 키워드 (검색이 본문까지 보므로, 본문에만 적힌 글도 찾는다)
#   match  : 제목/본문 앞부분에 모두 보여야 하는 조건 (다른 상품 글 제외용)
#   price_anchor : 상품명이 제목에 없을 때 대신 가격을 찾을 이름 (예: 제목의 "FIREBAT F1($254)")
#   alert  : 기준가 미만이면 알림
PRODUCTS = [
    {
        "key": "gmktec-k12", "name": "GMKtec K12",
        "anchor": _rx(r"(?<![a-z0-9])k[\s-]?12(?![0-9])"), "search": ["K12"],
        "match": [_rx(r"gmk\s?tec|gmk(?![a-z])|지엠케이|nucbox|누크박스|미니\s?pc|mini\s?pc")],
        "price_anchor": None, "alert": True,
    },
    {
        "key": "firebat-f1-7640hs", "name": "FIREBAT F1 7640HS",
        # "GMKtec M6 7640HS" 같은 다른 7640HS 제품과 구분하려고 F1 바로 뒤의 7640HS 만 인정
        "anchor": _rx(F1 + r"[^$()\n,/]{0,40}?" + V7640), "search": ["7640HS"],
        "match": [FIREBAT_RE], "price_anchor": F1_RE, "alert": False,
    },
    {
        "key": "firebat-f1-h255", "name": "FIREBAT F1 H255",
        "anchor": _rx(F1 + r"[^$()\n,/]{0,40}?" + VH255), "search": ["H255"],
        "match": [FIREBAT_RE], "price_anchor": F1_RE, "alert": False,
    },
    {
        # 세부 모델(7640HS/H255)을 알 수 없는 FIREBAT F1 글. 위 두 모델로 분류되지 않을 때만 기록.
        "key": "firebat-f1", "name": "FIREBAT F1 (모델 미표기)",
        "anchor": F1_RE, "search": ["FIREBAT", "파이어뱃"],
        "match": [], "price_anchor": None, "alert": False,
        "fallback_for": ["firebat-f1-7640hs", "firebat-f1-h255"],
    },
]

# 램/SSD 가 빠진 베어본 구성은 FIREBAT F1 비교 대상(완제품)과 가격대가 달라 기록하지 않는다.
BAREBONE_RE = _rx(r"베어\s?본|barebone")
NO_BAREBONE = {"firebat-f1-7640hs", "firebat-f1-h255", "firebat-f1"}

# 자동으로 걸러지지 않아 사람이 확인하고 뺀 글: (상품 key, 글번호) → 이유
MANUAL_EXCLUDE = {
    ("firebat-f1-7640hs", "100107"): "베어본 구성 (제목에 표기 없음, 사용자 확인)",
}

# ---------------------------------------------------------------- prices

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


def segments(text, anchor, product, limit):
    """anchor 뒤 ~ 다른 감시상품 이름 전까지 구간 (여러 상품 모음글 대비)."""
    others = [p["anchor"] for p in PRODUCTS if p is not product and p["anchor"] is not anchor]
    segs = []
    for m in anchor.finditer(text):
        end = min(len(text), m.end() + limit)
        for o in others:
            om = o.search(text, m.end(), end)
            if om:
                end = min(end, om.start())
        segs.append((m.end(), end))
    return segs


def title_price(title, anchor, product):
    """제목: '상품명($가격)' 형식 → 상품명 바로 뒤 첫 가격. 할인가/최저가 표기가 있으면 그것."""
    for lo, hi in segments(title, anchor, product, 60):
        prices = find_prices(title[lo:hi])
        finals = [p for p in prices if p[3] == "final"]
        if finals:
            return min(finals, key=lambda p: p[0]), lo
        if prices:
            return prices[0], lo
    return None, 0


def body_price(body, anchor, product):
    """본문: 상품명 뒤 구간에서 할인가/최저가 우선, 없으면 최저가."""
    prices = [(v, s, pos + lo, k) for lo, hi in segments(body, anchor, product, 800)
              for v, s, pos, k in find_prices(body[lo:hi])]
    finals = [p for p in prices if p[3] == "final"]
    return min(finals or prices, key=lambda p: p[0]) if prices else None


def analyze(title, body, product, mentioned=False):
    """해당 상품 글이면 dict, 아니면 None.
    mentioned: 통합검색이 (보이지 않는 본문까지 포함해) 이 상품명으로 이 글을 찾았는지."""
    full = title + "\n" + body
    if not (product["anchor"].search(full) or mentioned):
        return None
    if not all(r.search(full) for r in product["match"]):
        return None

    anchors = [product["anchor"]]
    # 제목에 이 모델 표기가 없을 때만 "FIREBAT F1" 로 가격을 찾는다.
    # 제목에 다른 모델(예: F1 H255)만 적혀 있으면 그 F1 은 다른 모델이므로 쓰지 않는다.
    other_variant_in_title = any(
        p["anchor"].search(title) for p in PRODUCTS
        if p is not product and p["price_anchor"] is not None)
    if product["price_anchor"] is not None and not product["anchor"].search(title) and not other_variant_in_title:
        anchors.append(product["price_anchor"])
    if product["key"] in NO_BAREBONE and is_barebone(title, body, product, anchors):
        return None
    for anchor in anchors:
        best, off = title_price(title, anchor, product)
        if best:
            return _result(best, "title", title, off, anchor is not product["anchor"])
    for anchor in anchors:
        best = body_price(body, anchor, product)
        if best:
            return _result(best, "body", body, 0, anchor is not product["anchor"])
    return {"price": None, "price_text": "", "source": "none", "context": "", "alias": False}


def is_barebone(title, body, product, anchors):
    """상품명 바로 뒤 구간(제목 80자, 본문 300자)에 '베어본' 이 있으면 True."""
    for text, limit in ((title, 80), (body, 300)):
        for anchor in anchors:
            for m in anchor.finditer(text):
                lo, hi = next(((a, b) for a, b in segments(text[m.start():], anchor, product, limit)), (0, 0))
                if BAREBONE_RE.search(text[m.start(): m.start() + hi]):
                    return True
    return False


def _result(best, source, src, off, alias=False):
    pos = best[2] + off
    ctx = src[max(0, pos - 120): pos + 120].replace("\n", " ")
    return {"price": best[0], "price_text": best[1], "source": source, "context": ctx, "alias": alias}


def analyze_post(post):
    """글 하나 → 상품별 결과 [(product, res)]"""
    results = {}
    for product in PRODUCTS:
        if product.get("fallback_for") and any(k in results for k in product["fallback_for"]):
            continue
        mentioned = product["key"] in post.get("hits", ())
        res = analyze(post["title"], post["body"], product, mentioned)
        if res:
            results[product["key"]] = (product, res)
    # 제목에 모델 없이 "FIREBAT F1($254)" 만 있고 본문에 두 모델이 다 나오면 어느 모델 가격인지 알 수 없다
    # → 두 모델 모두에서 빼고 '모델 미표기' 로 기록
    for key in [k for k in results if (k, post.get("no")) in MANUAL_EXCLUDE]:
        del results[key]
    alias_keys = [k for k, (p, r) in results.items() if r.get("alias") and p.get("price_anchor") is not None]
    if len(alias_keys) >= 2:
        for k in alias_keys:
            del results[k]
        generic = next(p for p in PRODUCTS if p.get("fallback_for"))
        res = analyze(post["title"], post["body"], generic, True)
        if res:
            results[generic["key"]] = (generic, res)
    return list(results.values())


# ---------------------------------------------------------------- collecting


def search(kw, page, stype, order):
    q = urllib.parse.quote(kw, encoding="cp949")
    url = SEARCH_URL.format(stype=stype, page=page, kw=q, board=BOARD_ID, order=order)
    found = parse_search(fetch(url))
    time.sleep(REQUEST_DELAY)
    if not found and page == 1:
        time.sleep(REQUEST_DELAY * 5)  # 연속 요청 제한일 수 있어 쉬었다가 한 번 더
        found = parse_search(fetch(url))
        time.sleep(REQUEST_DELAY)
    return found


def collect():
    """상품별 검색어로 통합검색 → {no: post}. post['hits'] = 검색에 걸린 상품 key 집합."""
    posts = {}
    for product in PRODUCTS:
        for kw in product["search"]:
            for stype, order in (SEARCH_MODES_BACKFILL if SINCE else SEARCH_MODES_RECENT):
                for page in range(1, (MAX_PAGES if SINCE else 1) + 1):
                    found = search(kw, page, stype, order)
                    print(f"search '{kw}' {stype}/{order} page {page}: {len(found)} posts")
                    for p in found:
                        posts.setdefault(p["no"], {**p, "hits": set()})["hits"].add(product["key"])
                    dates = [p["date"] for p in found if p["date"]]
                    if not found or (SINCE and order == "date" and dates and min(dates) < SINCE):
                        break
    if SINCE:
        posts = {no: p for no, p in posts.items() if not p["date"] or p["date"] >= SINCE}
    return posts


# ---------------------------------------------------------------- outputs

FIELDS = ["posted_at", "product", "post_no", "price_usd", "price_text", "source", "title", "url"]


def save_history(rows):
    """(상품, 글번호) 단위로 최신 값을 유지. 새 글이거나 가격이 바뀐 것만 반환."""
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    existing = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r.get("product", ""), r.get("post_no", ""))] = {k: r.get(k, "") for k in FIELDS}
    for key in MANUAL_EXCLUDE:
        existing.pop(key, None)
    changed = []
    for r in rows:
        key = (r["product"], r["post_no"])
        if key not in existing or existing[key]["price_usd"] != r["price_usd"]:
            changed.append(r)
        existing[key] = r
    with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(existing.values(), key=lambda r: (r["product"], r["posted_at"], r["post_no"])))
    return changed


def github_api(method, path, data=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}",
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or "null")


def _norm(t):
    return re.sub(r"\W", "", t)[:60]


def already_alerted(no, post_title):
    """같은 글, 또는 같은 제목으로 다시 올라온 글이면 이미 알린 것으로 본다."""
    issues = github_api("GET", "/issues?state=all&labels=price-alert&per_page=100")
    key = _norm(post_title)
    return any(f"#{no}]" in i["title"] or (key and key in _norm(i["title"])) for i in issues)


def notify(hit):
    where = "제목" if hit["source"] == "title" else "본문"
    if hit["price"] is None:
        title = f"[K12 가격 확인 필요] {hit['title'][:80]} [뽐뿌#{hit['post_no']}]"
        body = (
            "본문에 GMKtec K12 가 언급된 글인데, 가격이 본문 뒤쪽에 있어 자동으로 읽지 못했습니다.\n"
            "(해외 서버에서는 글 전체를 열 수 없어 검색결과의 본문 앞부분만 보입니다.)\n\n"
            f"- 글: {hit['url']} ({hit['posted_at']})\n"
            f"- 제목: {hit['title']}\n\n"
            "링크를 열어 K12 가격이 $%.0f 미만인지 확인해 주세요." % THRESHOLD_USD
        )
    else:
        title = f"[K12 ${hit['price']:.2f}] {hit['title'][:80]} [뽐뿌#{hit['post_no']}]"
        body = (
            f"GMKtec K12 가격이 **${hit['price']:.2f}** 로 기준가 ${THRESHOLD_USD:.0f} 미만입니다.\n\n"
            f"- 글: {hit['url']} ({hit['posted_at']})\n"
            f"- 제목: {hit['title']}\n"
            f"- 가격 표기: `{hit['price_text']}` ({where}에서 추출)\n"
            f"- 원화 환산 기준: 1 USD = {KRW_PER_USD:.0f} KRW\n\n"
            f"> …{hit['context']}…\n\n"
            "자동 추출이라 오인식일 수 있으니 글을 직접 확인해 주세요."
        )
    if DRY_RUN or not os.environ.get("GITHUB_TOKEN"):
        print("[DRY] would alert:", title)
        return
    if already_alerted(hit["post_no"], hit["title"][:80]):
        print("already alerted:", hit["post_no"])
        return
    create_alert_issue(title, body)
    print("ALERT issue created:", title)

    head = "K12 가격 확인 필요" if hit["price"] is None else f"GMKtec K12 ${hit['price']:.2f}"
    send_telegram(f"{head}\n{hit['title']}\n{hit['url']}")


def create_alert_issue(title, body):
    """저장소 주인에게 배정하고 @멘션한다. 배정/멘션된 이슈는 '직접 관련' 알림이라
    GitHub 모바일 앱 푸시와 메일이 기본 설정에서도 온다 (지켜보기만 한 저장소의 새 이슈는 푸시되지 않음)."""
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ["GITHUB_REPOSITORY"].split("/")[0]
    return github_api("POST", "/issues", {
        "title": title,
        "body": f"@{owner}\n\n{body}",
        "labels": ["price-alert"],
        "assignees": [owner],
    })


def send_telegram(msg):
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": tg_chat, "text": msg}).encode(),
            timeout=30,
        )


def send_test_alert():
    """알림이 실제로 오는지 확인용. 실제 알림과 같은 방식(price-alert 이슈)으로 보낸다."""
    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    title = f"[테스트 알림] 가격 알림이 잘 오는지 확인 ({now} KST)"
    body = (
        "가격 감시에서 보낸 **시험 알림**입니다. 실제 특가 알림도 이 형태로 옵니다.\n\n"
        "- 이 이슈를 GitHub 알림(앱 푸시/메일)으로 받으셨다면 설정이 끝난 것입니다.\n"
        "- 확인하셨으면 이 이슈는 닫아 주세요 (Close issue)."
    )
    if DRY_RUN or not os.environ.get("GITHUB_TOKEN"):
        print("[DRY] would send test alert:", title)
        return 0
    issue = create_alert_issue(title, body)
    print("TEST issue created:", issue.get("html_url"))
    send_telegram(f"[테스트 알림] 가격 알림 확인용\n{issue.get('html_url')}")
    return 0


# ---------------------------------------------------------------- main


def main():
    if os.environ.get("TEST_ALERT") == "true":
        return send_test_alert()
    posts = collect()
    if not posts:
        print("ERROR: 검색 결과가 하나도 없습니다 (차단 또는 페이지 구조 변경).")
        return 1

    alert_since = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=ALERT_DAYS)).strftime("%Y-%m-%d")
    rows, hits = [], []
    for post in posts.values():
        for product, res in analyze_post(post):
            price = "" if res["price"] is None else f"{res['price']:.2f}"
            print(f"{post['date']} {product['name']}: {price or '가격 미확인'} ({res['price_text']}) {post['title']}")
            row = {"posted_at": post["date"], "product": product["key"], "post_no": post["no"],
                   "price_usd": price, "price_text": res["price_text"], "source": res["source"],
                   "title": post["title"], "url": post["url"]}
            rows.append(row)
            # 알림은 최근 글만 (과거 글 수집 시에는 알리지 않음)
            if product["alert"] and not SINCE and post["date"] >= alert_since:
                if res["price"] is not None and res["price"] < THRESHOLD_USD:
                    hits.append({**res, **row})
                elif res["price"] is None:
                    # 본문 뒤쪽에만 K12 가 있어 가격을 못 읽은 글 → 직접 확인하도록 알림
                    hits.append({**res, **row})

    changed = save_history(rows)
    print(f"posts {len(posts)}, product rows {len(rows)}, new/changed {len(changed)}, alerts {len(hits)}")
    for h in hits:
        notify(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
