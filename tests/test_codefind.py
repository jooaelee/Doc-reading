# -*- coding: utf-8 -*-
"""이름으로 KEROL 코드를 찾는 도구의 잠금.

🔴 이 자리가 조용히 망가지면 아무도 모른다. 코드를 틀리게 채워도 화면에는 코드가 채워져
   보이고, KEROL 은 그 코드를 받아 준다. 서류와 대조할 방법이 없다. 그래서 잠금을 건다.

     하나. 값을 지어내지 않는다. 못 찾으면 빈손으로 두고 사람에게 넘긴다.
     둘.   이름이 같고 코드가 다른 거래처는 주소 없이 고르지 않는다.
     셋.   담당자가 확정한 짝이 있으면 그것을 먼저 쓴다. 회의에서 말한 "학습"이 이것이다.
     넷.   표기가 흔들려도 찾는다. 대소문자, 문장 부호, 법인격, 특수 공백.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(ROOT, "tools", "codefind.py")
    spec = importlib.util.spec_from_file_location("codefind", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codefind"] = mod
    spec.loader.exec_module(mod)
    return mod


cf = _load()

# 시험용 거래처. 실제 고객 이름은 저장소에 넣지 않는다.
MASTER = [
    {"code": "NDCC001", "name": "NDEC CO., LTD.", "address": "88 SEONGSAN-RO, CHANGWON, KOREA"},
    {"code": "NDCC002", "name": "NDEC CO., LTD.", "address": "120 GASAN DIGITAL 1-RO, SEOUL, KOREA"},
    {"code": "NDEL001", "name": "NDEC ELECTRONICS LIMITED", "address": "KOWLOON, HONG KONG"},
    {"code": "HANA001", "name": "HANA PRECISION INC.", "address": "12 SANDAN-RO, ASAN, KOREA"},
    {"code": "BLUE001", "name": "BLUE OCEAN LOGISTICS GMBH", "address": "HAMBURG, GERMANY"},
]


class 정규화(unittest.TestCase):
    def test_법인격과_문장_부호를_걷어낸다(self):
        self.assertEqual(cf.core("NDEC CO., LTD."), cf.core("ndec co ltd"))
        self.assertEqual(cf.core("HANA PRECISION INC."), cf.core("Hana Precision, Inc"))

    def test_특수_공백과_기호를_고른다(self):
        self.assertEqual(cf.normalize("L&F CO., LTD."), cf.normalize("L & F CO., LTD."))

    def test_알맹이가_비면_정규화한_것을_쓴다(self):
        self.assertTrue(cf.core("CO., LTD."))


class 찾기(unittest.TestCase):
    def setUp(self):
        self.idx = cf.CodeIndex(MASTER)

    def test_표기가_흔들려도_찾는다(self):
        for q in ("hana precision inc.", "HANA PRECISION, INC", "Hana  Precision", "HANA PRECISION INC"):
            with self.subTest(q=q):
                self.assertEqual(self.idx.decide(q)["code"], "HANA001")

    def test_없는_이름은_빈손으로_둔다(self):
        d = self.idx.decide("전혀 다른 회사 이름입니다")
        self.assertEqual(d["status"], "없음")
        self.assertIsNone(d["code"])
        self.assertEqual(d["candidates"], [])

    def test_빈_이름은_후보도_내지_않는다(self):
        self.assertEqual(self.idx.lookup(""), [])
        self.assertEqual(self.idx.lookup(None), [])

    def test_이름이_같으면_주소_없이_고르지_않는다(self):
        d = self.idx.decide("NDEC CO.,LTD")
        self.assertEqual(d["status"], "선택")
        self.assertIsNone(d["code"])
        self.assertEqual({c["code"] for c in d["candidates"][:2]}, {"NDCC001", "NDCC002"})

    def test_주소가_있으면_가른다(self):
        self.assertEqual(self.idx.decide("NDEC CO.,LTD", "GASAN DIGITAL 1-RO SEOUL")["code"], "NDCC002")
        self.assertEqual(self.idx.decide("NDEC CO.,LTD", "CHANGWON KOREA")["code"], "NDCC001")

    def test_주소가_도움이_안_되면_다시_사람에게_넘긴다(self):
        self.assertEqual(self.idx.decide("NDEC CO.,LTD", "KOREA")["status"], "선택")

    def test_닮았지만_다른_회사를_확정하지_않는다(self):
        d = self.idx.decide("NDEC ELECTRONICS")
        self.assertEqual(d["code"], "NDEL001")
        d2 = self.idx.decide("BLUE OCEAN")
        self.assertIn(d2["status"], ("확정", "추천"))
        self.assertEqual(d2["candidates"][0]["code"], "BLUE001")


class 확정_매핑(unittest.TestCase):
    def test_저장한_짝을_먼저_쓴다(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "learned.json")
            idx = cf.CodeIndex(MASTER, learned=p)
            self.assertEqual(idx.decide("NDEC CO.,LTD")["status"], "선택")
            idx.learn("NDEC CO.,LTD", "NDCC002")
            got = idx.decide("NDEC CO.,LTD")
            self.assertEqual(got["status"], "확정")
            self.assertEqual(got["code"], "NDCC002")
            self.assertTrue(os.path.exists(p))
            again = cf.CodeIndex(MASTER, learned=p)      # 다시 띄워도 남아 있다
            self.assertEqual(again.decide("NDEC CO.,LTD")["code"], "NDCC002")
            with open(p, encoding="utf-8") as f: saved = json.load(f)
            self.assertEqual(saved.popitem()[1]["count"], 1)

    def test_확정_매핑이_없으면_파일을_만들지_않는다(self):
        idx = cf.CodeIndex(MASTER)
        self.assertEqual(idx.learned, {})


class 시험_도구(unittest.TestCase):
    def test_흔들린_이름을_만든다(self):
        v = cf.variants("NDEC CO., LTD.")
        self.assertGreaterEqual(len(v), 6)
        self.assertTrue(all(isinstance(s, str) and s for s, _ in v))

    def test_흔들림_시험이_숫자를_낸다(self):
        r = cf.evaluate(MASTER)
        self.assertEqual(r["entries"], len(MASTER))
        self.assertGreater(r["queries"], 0)
        self.assertLessEqual(r["top1"], r["queries"])
        # 이름이 같은 둘이 섞여 있어도 셋 안에는 든다
        self.assertGreaterEqual(r["topk"] / r["queries"], 0.9)


class 저장소_데이터(unittest.TestCase):
    def test_KEROL_이름으로_시험이_돈다(self):
        db = os.path.join(ROOT, "data", "kerol.sqlite")
        if not os.path.exists(db): self.skipTest("kerol.sqlite 가 없습니다")
        entries = cf.kerol_entries(ROOT)
        self.assertGreater(len(entries), 50)
        r = cf.evaluate(entries[:40])
        self.assertGreaterEqual(r["top1"] / r["queries"], 0.9)


if __name__ == "__main__":
    unittest.main()
