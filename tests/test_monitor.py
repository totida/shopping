import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor  # noqa: E402

K12, F1_7640, F1_H255, F1_ANY = monitor.PRODUCTS


def analyze(title, body, product=K12):
    return monitor.analyze(title, body, product)


class AnalyzeTest(unittest.TestCase):
    def test_title_price(self):
        r = analyze("[알리익스프레스] GMKtec K12 미니PC 베어본 ($189.99/무료)", "본문")
        self.assertAlmostEqual(r["price"], 189.99)
        self.assertEqual(r["source"], "title")

    def test_body_only_mention(self):
        body = "오늘 미니PC 특가 모음\nGMKtec K12 32GB/1TB 쿠폰 적용시 $179 입니다.\n다른상품 $50"
        r = analyze("[알리] 미니PC 모음 특가", body)
        self.assertAlmostEqual(r["price"], 179)
        self.assertEqual(r["source"], "body")

    def test_body_ignores_far_prices_of_other_products(self):
        filler = "가" * 600
        body = "비링크 SER8 $150\n" + filler + "\nGMKtec K12 가격은 $259"
        r = analyze("[알리] 미니PC 모음", body)
        self.assertAlmostEqual(r["price"], 259)

    def test_coupon_amount_not_price(self):
        r = analyze("GMKtec K12 (US $239.00/무료)", "$30 할인 쿠폰")
        self.assertAlmostEqual(r["price"], 239)

    def test_krw(self):
        r = analyze("[알리] GMKtec K12 (259,000원/무료)", "")
        self.assertAlmostEqual(r["price"], round(259000 / monitor.KRW_PER_USD, 2))

    def test_unrelated(self):
        self.assertIsNone(analyze("[알리] 기계식 키보드 ($45)", "k120 아님"))
        self.assertIsNone(analyze("[알리] 로지텍 K120 키보드 ($12)", ""))

    def test_screenshot_7640hs_uses_final_price_not_card_discount(self):
        body = ("FIREBAT F1 미니 7640HS PC\n판매가 : $398.28\n할인코드 $36 ( BTCW36 )\n"
                "코인할인 $7.96\n카드할인 $100 ( 삼성비자카드, 롯데카드 )\n할인가 $254.32\n"
                ">>>>>> FIREBAT F1 미니 7640HS PC 바로가기")
        r = analyze("[알리] 미니PC 특가 모음", body, F1_7640)
        self.assertAlmostEqual(r["price"], 254.32)
        self.assertIsNone(analyze("[알리] 미니PC 특가 모음", body, F1_H255))

    def test_screenshot_multi_product_post(self):
        body = ("GMKtec M8 미니 PC AMD Ryzen 5 PRO 6650H\n최저가 $199\n>>>>>> GMKtec M8 바로가기\n"
                "FIREBAT F1 미니 7640HS PC\n판매가 : $398.28\n카드할인 $100\n할인가 $254.32\n"
                "FIREBAT F1 미니 PC AMD Ryzen 7 H255 LPDDR5 16GB RAM 512GB SSD\n"
                "상세페이지 가격: $449.91\n코드할인 $36\n코인할인 $9\n"
                "결제 할인 $100< 삼성iD SELECT UP 비자 >\n최저가: $304.91")
        self.assertAlmostEqual(analyze("[알리] 미니PC 모음", body, F1_7640)["price"], 254.32)
        self.assertAlmostEqual(analyze("[알리] 미니PC 모음", body, F1_H255)["price"], 304.91)
        self.assertIsNone(analyze("[알리] 미니PC 모음", body, K12))

    def test_parse_search_and_title_prices(self):
        page = (
            '<div class="results_board"><div class="conts">\n'
            "<a href='https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu8&no=99347&keyword=K12'>"
            "<div class='thumb'></div></a> <div class=\"content\">\n"
            "<span class=\"title\"><a href=/zboard/view.php?id=ppomppu8&no=99347&keyword=K12>"
            "[미니PC특가] GMKtec G3S($116) FIREBAT F1($254) GMKtec M8($200)"
            "<font class='comment-cnt'>0</font></a></span>\n"
            "<p style=\"height:45px\"><a href=/zboard/view.php?id=ppomppu8&no=99347&keyword=K12>"
            "GMKtec G3S 미니 PC 상세페이지 가격: $159.57코드할인 $15 &lt; SRMG15 &gt;최저가: $116.38&nbsp;</a></p>\n"
            "<p class=\"desc\"><span>[알리뽐뿌]</span><span>조회수: 614</span> | <span>2026.09.15</span> |</p>"
            "</div></div>"
        )
        posts = monitor.parse_search(page)
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual((post["no"], post["date"]), ("99347", "2026-09-15"))
        self.assertTrue(post["title"].startswith("[미니PC특가] GMKtec G3S($116)"))

        # 검색 '7640HS' 에 걸림 → 제목의 "FIREBAT F1($254)" 가 7640HS 가격
        post["hits"] = {"firebat-f1-7640hs", "gmktec-k12"}
        got = {p["key"]: r["price"] for p, r in monitor.analyze_post(post)}
        self.assertEqual(got, {"firebat-f1-7640hs": 254, "gmktec-k12": None})

        # 세부 모델 검색에 안 걸리면 모델 미표기 F1 으로 기록
        post["hits"] = {"firebat-f1"}
        got = {p["key"]: r["price"] for p, r in monitor.analyze_post(post)}
        self.assertEqual(got, {"firebat-f1": 254})

    def test_title_first_price_after_name(self):
        r = analyze("[알리] GMKtec K12($189) GMKtec M8($150) /무료", "")
        self.assertAlmostEqual(r["price"], 189)

    def test_other_7640hs_product_in_title(self):
        title = "[특가마지막날] GMKtec M6 7640HS ($164), FIREBAT F1 7640HS ($312), 샤오신 GT13($281)"
        self.assertAlmostEqual(analyze(title, "", F1_7640)["price"], 312)

    def test_h255_without_firebat_prefix(self):
        title = "[재입고] GMKtec M6 7640HS 미니PC($164), FIREBAT R3 7430U($216), F1 H255($340)"
        self.assertAlmostEqual(analyze(title, "", F1_H255)["price"], 340)
        self.assertIsNone(analyze(title, "", F1_7640))

    def test_ambiguous_f1_goes_to_generic(self):
        post = {"no": "1", "title": "[미니PC특가] GMKtec G3S($116) FIREBAT F1($254) GMKtec M8($200)",
                "body": "", "hits": {"firebat-f1-7640hs", "firebat-f1-h255"}}
        got = {p["key"]: r["price"] for p, r in monitor.analyze_post(post)}
        self.assertEqual(got, {"firebat-f1": 254})

    def test_alias_ignores_other_variant(self):
        post = {"no": "2", "title": "[특가마지막날] GMKtec M6 7640HS ($164), FIREBAT F1 7640HS ($312)",
                "body": "", "hits": {"firebat-f1-7640hs", "firebat-f1-h255"}}
        got = {p["key"]: r["price"] for p, r in monitor.analyze_post(post)}
        self.assertEqual(got, {"firebat-f1-7640hs": 312, "firebat-f1-h255": None})

    def test_ryzen_variant_names(self):
        self.assertAlmostEqual(analyze("FIREBAT F1 미니 PC AMD Ryzen 5 ($192)무배", "", F1_7640)["price"], 192)
        self.assertAlmostEqual(analyze("FIREBAT F1 미니 PC AMD Ryzen 7 H255 (471,954원/무료)", "", F1_H255)["price"],
                               round(471954 / monitor.KRW_PER_USD, 2))

    def test_alias_not_used_when_title_names_other_variant(self):
        post = {"no": "3", "title": "[미니PC] FIREBAT R3 7430U($216), F1 H255 ($340)/무료",
                "body": "FIREBAT F1 미니 PC 판매가 $361.15", "hits": {"firebat-f1-7640hs", "firebat-f1-h255"}}
        got = {p["key"]: r["price"] for p, r in monitor.analyze_post(post)}
        self.assertEqual(got, {"firebat-f1-h255": 340, "firebat-f1-7640hs": None})


if __name__ == "__main__":
    unittest.main()
