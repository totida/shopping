import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor  # noqa: E402

K12, F1_7640, F1_H255 = monitor.PRODUCTS


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

    def test_list_ids(self):
        page = ('<a href="bbs_view.php?id=ppomppu8&amp;no=12345&amp;page=1">a</a>'
                '<a href="/new/bbs_view.php?id=ppomppu&no=999">b</a>'
                '<a href="bbs_view.php?id=ppomppu8&no=12346">c</a>')
        self.assertEqual(monitor.list_post_ids(page), ["12345", "12346"])


if __name__ == "__main__":
    unittest.main()
