"""규칙과 학습 잠금. AI 를 부르지 않는다. 작업 폴더는 시험마다 새로 만든다."""
import json
import os

import pytest

import wuxi_paths
import wuxi_rules as R
import wuxi_learn as L
import wuxi_engine as E


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    R._cache.update(key=None, state=None)
    yield tmp_path


def ex_of():
    return {"shipper_name": "ACME PARTS LTD",
            "ci_lines": [{"seq": 1, "invoice_no": "I1", "description": "PUMP SEAL KIT 300MM", "sap_code": None, "unit": "PCS.",
                          "country_of_origin": "KOREA", "po_no": "4500000001", "po_item_no": "00010", "amount": 10.0},
                         {"seq": 2, "invoice_no": "I1", "description": "ROBOT BLADE", "sap_code": "F1", "unit": "EA",
                          "country_of_origin": "JAPAN", "po_no": "4500000001", "po_item_no": "00020", "amount": 5.0}],
            "pl_lines": [{"seq": 1, "unit": "PCS."}], "invoice_totals": [{"invoice_no": "I1", "total": 15.0}]}


def test_기본값은_빠짐_검사_넷과_필수_칸_셋():
    pol = R.check_policy(None)
    assert all(pol[c]["enabled"] for c in R.CHECKS) and pol["amount"]["tolerance"] == 0.01
    assert R.required_columns(None) == [["Purchase Order"], ["Material", "Vendor Part No."], ["B/L quantity"]]


def test_문장은_조사가_어긋나지_않는다():
    assert R.josa("품명", "으로로") == "으로" and R.josa("SAP 코드", "으로로") == "로" and R.josa("원산지", "을를") == "를"
    r = R.add("header", "*", {"field": "description", "header": "Goods"})
    assert R.describe(r) == "표의 「Goods」 열을 품명으로 읽습니다"


def test_공급사_규칙이_공통_규칙을_이긴다():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    R.add("code", "*", {"on": "desc", "key": "PUMP SEAL KIT 300MM", "field": "sap_code", "value": "COMMON1"})
    R.add("code", sid, {"on": "desc", "key": "pump seal kit 300mm", "field": "sap_code", "value": "SUP1"})
    R.add("alias", "*", {"field": "unit", "from": "PCS.", "to": "PCS"})
    ex = ex_of()
    trace = R.apply_post(ex, sid)
    assert ex["ci_lines"][0]["sap_code"] == "SUP1"
    assert ex["ci_lines"][0]["unit"] == "PCS" and ex["pl_lines"][0]["unit"] == "PCS"
    assert {t["kind"] for t in trace} == {"code", "alias"}
    assert ex["ci_lines"][1]["sap_code"] == "F1"          # 빈 칸만 채우는 규칙은 있는 값을 안 건드린다


def test_포함_맞추기와_바꾸기_방식():
    R.add("code", "*", {"on": "desc", "key": "BLADE", "match": "contains", "field": "sap_code", "value": "B9", "mode": "replace"})
    ex = ex_of()
    R.apply_post(ex, None)
    assert ex["ci_lines"][1]["sap_code"] == "B9" and ex["ci_lines"][0]["sap_code"] is None


def test_공급사는_송하인_이름으로_자동으로_생기고_다음엔_글로_알아본다():
    sid = R.supplier_for("", {"shipper_name": "NEW VENDOR CO"})
    assert sid and R.suppliers()[sid]["source"] == "auto"
    assert R.identify_supplier("COMMERCIAL INVOICE\nNEW VENDOR CO\nSEOUL") == sid


def test_빠짐_검사_공급사_설정과_필수_칸_빼기():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    R.add("check", sid, {"check": "lines_cn", "enabled": False})
    R.add("required", sid, {"columns": ["B/L quantity"], "required": False})
    assert R.check_policy(sid)["lines_cn"]["enabled"] is False and R.check_policy(None)["lines_cn"]["enabled"] is True
    assert ["B/L quantity"] not in R.required_columns(sid)


def test_후보_반영_격상_해제_거절과_이력():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    c = R.upsert_candidate("alias", sid, {"field": "country_of_origin", "from": "JAPAN", "to": "JP"}, {"job": "j1", "row": 1})
    assert c["status"] == "candidate"
    again = R.upsert_candidate("alias", sid, {"field": "country_of_origin", "from": "japan", "to": "JP"}, {"job": "j2", "row": 1})
    assert again["id"] == c["id"] and len(again["evidence"]) == 2
    R.change(c["id"], "activate", by="시험")
    up = R.change(c["id"], "promote", by="시험", reason="모든 공급사 같음")
    assert up["scope"] == R.COMMON and up["promoted_from"] == sid
    R.change(c["id"], "off")
    assert R.get(c["id"])["status"] == "off"
    bad = R.upsert_candidate("alias", sid, {"field": "unit", "from": "EA", "to": "EAS"}, {"job": "j3", "row": 0})
    R.change(bad["id"], "reject")
    assert R.upsert_candidate("alias", sid, {"field": "unit", "from": "EA", "to": "EAS"}, {"job": "j4", "row": 0}) is None
    ev = [h["event"] for h in R.history()]
    assert {"candidate", "activate", "promote", "off", "reject"} <= set(ev)
    with pytest.raises(ValueError):
        R.change(R.get("d_check_amount")["id"], "delete")


def test_판독_지침에_더할_글():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    assert R.prompt_addendum(sid) == ""
    R.add("header", sid, {"field": "sap_code", "header": "Customer Item"})
    R.add("guide", sid, {"text": "Read the second line of each row."})
    add = R.prompt_addendum(sid)
    assert '"Customer Item"' in add and "Read the second line" in add and R.prompt_addendum(None) == ""


def test_내보내기와_가져오기(tmp_path):
    R.add("alias", "*", {"field": "unit", "from": "PCS.", "to": "PCS"})
    data = R.export()
    R._cache.update(key=None, state=None)
    os.remove(os.path.join(wuxi_paths.WORK, "rules.json"))
    assert not any(r["kind"] == "alias" for r in R.all_rules())
    assert R.import_(data)["rules"] >= 1
    assert any(r["kind"] == "alias" for r in R.all_rules())


def test_빠짐_검사는_꺼_둔_검사를_세지_않는다():
    ex = ex_of()
    ex["pl_lines"] = [{"seq": 1}]
    pol = R.check_policy(None)
    assert E.completeness(ex, [], pol)["verdict"] == "빠진 줄 의심"
    pol["lines_pl"]["enabled"] = False
    c = E.completeness(ex, [], pol)
    assert c["verdict"] == "이상 없음" and {x["check"]: x["state"] for x in c["checks"]}["lines_pl"] == "off"
    ex["invoice_totals"] = [{"invoice_no": "I1", "total": 15.4}]
    pol["amount"]["tolerance"] = 0.5
    assert E.completeness(ex, [], pol)["verdict"] == "이상 없음"


def job_of(items, supplier=None):
    cols = {"gerp": ["Item", "Purchase Order", "Material", "Vendor Part No.", "B/L quantity"],
            "extra": ["품명", "단위", "원산지", "Sap Code", "Part No."]}
    return {"id": "a" * 32, "supplier": supplier, "pdfs": [], "payload": {"items": items, "cols": cols,
                                                                          "completeness": {"checks": []}}}


def test_칸_수정에서_후보가_생긴다():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    orig = [{"Item": "00010", "Purchase Order": "45", "Material": "", "Vendor Part No.": "", "B/L quantity": "1",
             "품명": "PUMP SEAL", "단위": "PCS.", "원산지": "KOREA", "Sap Code": "", "Part No.": ""},
            {"Item": "00020", "Purchase Order": "45", "Material": "X1", "Vendor Part No.": "VP-9", "B/L quantity": "2",
             "품명": "BLADE", "단위": "EA", "원산지": "JAPAN", "Sap Code": "X1", "Part No.": "VP-9"}]
    job = job_of(orig, sid)
    final = [dict(r, _i=i) for i, r in enumerate(orig)]
    final[0]["Material"] = "P3187097"
    final[0]["단위"] = "PCS"
    final[1]["Material"] = "X2"
    final.append({"품명": "MISSING", "B/L quantity": "1"})
    out = L.record_edits(job, final)
    assert out["cells"] == 3 and out["added"] == 1
    texts = {r["kind"]: R.describe(r) for r in R.all_rules() if r["status"] == "candidate"}
    assert set(texts) == {"code", "alias", "guide"}
    cands = [r for r in R.all_rules() if r["status"] == "candidate" and r["kind"] == "code"]
    ons = {(r["data"]["on"], r["data"]["key"], r["data"]["value"], r["data"]["mode"]) for r in cands}
    assert ("desc", "PUMP SEAL", "P3187097", "fill") in ons and ("part", "VP-9", "X2", "replace") in ons


def test_미리보기는_확정값과_맞는_칸과_다른_칸을_센다(home):
    items = [{"Material": "F1", "품명": "PUMP SEAL", "Vendor Part No.": ""}, {"Material": "F1", "품명": "PUMP SEAL", "Vendor Part No.": ""}]
    job = job_of(items)
    job["status"] = "done"
    final = [dict(items[0], _i=0, Material="NEW"), dict(items[1], _i=1)]
    L.save_final(job, final)
    rule = {"kind": "code", "scope": "*", "data": {"on": "desc", "key": "PUMP SEAL", "match": "exact", "field": "sap_code",
                                                   "value": "NEW", "mode": "replace"}}
    pv = L.preview(rule, [job])
    assert pv["changed"] == 2 and pv["good"] == 1 and pv["bad"] == 1


def test_빠진_줄_없음_판정은_검사_끄기_후보를_만든다():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    job = job_of([], sid)
    job["payload"]["completeness"] = {"checks": [{"check": "lines_cn", "name": "통관표 줄 수", "state": "bad", "note": "7줄, 14줄"}]}
    out = L.review(job, "ok")
    r = R.get(out["candidates"][0])
    assert r["kind"] == "check" and r["data"] == {"check": "lines_cn", "enabled": False} and r["scope"] == sid


def test_원본_표에서_열_이름을_찾는다(tmp_path):
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((50, 80), "No   Customer Item   Description")
    pg.insert_text((50, 100), "1    CX-10001        PUMP")
    pg.insert_text((50, 116), "2    CX-10002        BLADE")
    f = str(tmp_path / "t.pdf")
    doc.save(f)
    assert L.find_header([f], "CX-10002") == "Customer Item"


def test_확정은_정답_자료를_남긴다(home):
    job = job_of([{"Material": "A", "품명": "X"}])
    L.confirm(job, [{"Material": "A", "품명": "X", "_i": 0}])
    assert job["confirmed"] and os.path.isfile(os.path.join(wuxi_paths.WORK, "jobs", "a" * 32, "golden.json"))
