# -*- coding: utf-8 -*-
"""구간별 칸 설명을 잠근다.

🔴 왜 잠그나. Upstage Information Extract 는 프롬프트를 받지 않고 스키마의 칸 설명만 읽는다.
   그 설명이 항공 기준으로만 쓰여 있던 동안 해상에서 편명 칸이 1.5% 였고 대화 모델은 96.2%
   였다. 격차가 아홉 칸에 퍼져 있지 않고 그 칸과 운송장 번호 둘에 전부 몰려 있었다.
   원인이 제품이 아니라 우리 설명이었다. 같은 일이 다시 생기지 않게 시험으로 못 박는다.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import docpipe as D


class 구간별_설명(unittest.TestCase):
    def tearDown(self):
        D.set_leg(None)

    def test_해상에서_갈렸던_두_칸이_덮인다(self):
        """실측이 지목한 자리다. 이 둘이 없으면 시험이 실패해야 한다."""
        for k in ("flight1", "hawb"):
            self.assertIn(k, D.FIELD_DOC_SEA)
            self.assertNotEqual(D.FIELD_DOC_SEA[k], D.FIELD_DOC[k])

    def test_항공_문구가_해상_설명에_안_남는다(self):
        sea = D.field_doc("sea")
        for k, v in sea.items():
            with self.subTest(k=k):
                if k in D.FIELD_DOC_SEA:
                    self.assertNotIn("air waybill", v.lower())
                    self.assertNotIn("flight number", v.lower())

    def test_해상_설명이_스키마에_실제로_붙는다(self):
        D.set_leg("sea")
        self.assertIn("Vessel name", D.SCHEMA["properties"]["flight1"]["description"])
        self.assertIn("Vessel name", D.SCHEMA_STRICT["properties"]["flight1"]["description"])

    def test_항공은_안_바뀐다(self):
        """설명을 가른 것이 해상만 고쳤다는 것을 이 시험이 말해 준다."""
        base = dict(D.FIELD_DOC)
        D.set_leg("air")
        self.assertEqual(D.field_doc(), base)
        self.assertEqual(D.SCHEMA["properties"]["flight1"]["description"], base["flight1"])

    def test_도장이_판을_가른다(self):
        D.set_leg(None)
        self.assertEqual(D.SCHEMA_VERSION, "s2")
        D.set_leg("air")
        self.assertEqual(D.SCHEMA_VERSION, "s3")
        D.set_leg("sea")
        self.assertEqual(D.SCHEMA_VERSION, "s3")
        D.set_schema_desc(False)
        self.assertEqual(D.SCHEMA_VERSION, "s1")
        D.set_schema_desc(True)

    def test_안_쓰는_구간_이름은_막는다(self):
        with self.assertRaises(ValueError):
            D.set_leg("rail")


if __name__ == "__main__":
    unittest.main()
