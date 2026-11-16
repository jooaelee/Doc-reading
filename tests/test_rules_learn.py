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


def _gerp_rows(ex):
    import gerp_xlsx
    return gerp_xlsx.tables_for([{"case": "T1", "ex": ex, "verify": []}])["items"]


def test_자재_코드_채우기는_Material_로_가지_않는_값을_덮어쓴다():
    """DUPONT 는 자기 11자리 번호를 자재 코드 칸에 읽어 Material 이 비었다. 사람이 넣은 R2117286 이 실제 판독에서도 들어가야 한다."""
    R.add("code", "*", {"on": "desc", "key": "ULTRAFILL A-3003A", "field": "material_code", "value": "R2117286"})
    R.add("code", "*", {"on": "desc", "key": "ROBOT BLADE", "field": "sap_code", "value": "R1111111"})
    ex = ex_of()
    ex["ci_lines"][0].update(description="ULTRAFILL A-3003A", material_code="00011047587")
    ex["ci_lines"][1]["sap_code"] = "P3024498"
    R.apply_post(ex, None)
    assert ex["ci_lines"][0]["material_code"] == "R2117286"
    assert ex["ci_lines"][1]["sap_code"] == "P3024498"         # Material 로 가는 모양의 값은 채우기 규칙이 안 건드린다
    assert _gerp_rows(ex)[0]["Material"] == "R2117286"


def test_규칙이_정한_자재_코드는_모양을_따지지_않고_Material_로_간다():
    """NOVA 는 공급사 품번(T-092-0112MC), VERSUM 은 숫자 코드(2310685)를 정답 Material 로 확정했다."""
    R.add("code", "*", {"on": "desc", "key": "PUMP SEAL KIT 300MM", "field": "sap_code", "value": "T-092-0112MC"})
    R.add("code", "*", {"on": "desc", "key": "ROBOT BLADE", "field": "sap_code", "value": "2310685"})
    ex = ex_of()
    ex["ci_lines"][1]["sap_code"] = "2310685"                  # 이미 같은 값을 읽었어도 규칙이 정한 값으로 본다
    assert _gerp_rows(ex_of())[0]["Material"] == ""            # 규칙이 없으면 이 모양은 Material 로 가지 않는다
    trace = R.apply_post(ex, None)
    rows = _gerp_rows(ex)
    assert rows[0]["Material"] == "T-092-0112MC" and rows[1]["Material"] == "2310685"
    assert [t["line"] for t in trace] == [0]                   # 값이 같은 줄은 바꾼 자리로 세지 않는다


ENF_HEAD = ("* INV NO.: 150909261002\n* PO NO.: 150909261002\n* DANGEROUS GOODS\n"
            "9) Your PO No.\nAttn: A.R.KIM   TEL: +82-31-881-8251\n4303488609\nSeller:\n"
            "4) Port of Loading\nBUSAN, KOREA 4303488609\n")


def test_서류_머리에만_찍힌_PO_를_품목_줄에_넣는다():
    """ENF 는 「9) Your PO No.」 아래 「Attn:」 줄 다음에 PO 가 있고, 운송장의 「* PO NO.」 는 인보이스 번호다."""
    ex = ex_of()
    for x in ex["ci_lines"] + ex["pl_lines"]:
        x["po_no"] = None
    assert E.header_po(ex, ENF_HEAD) == "4303488609"
    assert {x["po_no"] for x in ex["ci_lines"] + ex["pl_lines"]} == {"4303488609"}
    assert ex["ci_lines"][0]["_po_from"] == "서류 머리"
    ex2 = ex_of()
    for x in ex2["ci_lines"] + ex2["pl_lines"]:
        x["po_no"] = None
    assert E.header_po(ex2, "CUSTOMER P/O NO:\n4353597718 (YND-B100 19200LT)") == "4353597718"
    ex3 = ex_of()
    for x in ex3["ci_lines"]:
        x["po_no"] = None
    ex3["pl_lines"][0]["po_no"] = "150909261002"              # 운송장의 인보이스 번호를 포장명세서 줄에 넣은 경우
    assert E.header_po(ex3, ENF_HEAD) == "4303488609"
    assert ex3["ci_lines"][1]["po_no"] == "4303488609" and ex3["pl_lines"][0]["po_no"] == "150909261002"


def test_머리_PO_는_줄에_PO_가_있거나_둘_이상이면_넣지_않는다():
    ex = ex_of()
    assert E.header_po(ex, ENF_HEAD) is None and ex["pl_lines"][0].get("po_no") is None   # 인보이스 줄에 이미 있음
    ex2 = ex_of()
    for x in ex2["ci_lines"]:
        x["po_no"] = None
    assert E.header_po(ex2, "PO No. 4303488609\nPO No. 4303488610\n") is None
    assert E.header_po(ex2, "PORT OF LOADING\n4303488609\n") is None                   # PORT 의 PO 는 라벨이 아니다


def test_부품_번호를_바꿔도_거기서_온_Material_은_남는다():
    """DUPONT 는 하이닉스 번호(R3180486)를 부품 번호 칸에 읽었고, 정답 부품 번호는 00012113741 이다."""
    R.add("code", "*", {"on": "desc", "key": "SOLDERON SN 6000", "field": "part_no", "value": "00012113741", "mode": "replace"})
    ex = ex_of()
    ex["ci_lines"][0].update(description="SOLDERON SN 6000", part_no="R3180486")
    assert _gerp_rows(ex)[0]["Material"] == "R3180486"
    R.apply_post(ex, None)
    row = _gerp_rows(ex)[0]
    assert row["Material"] == "R3180486" and row["Vendor Part No."] == "00012113741"


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


def _pdf(tmp_path, lines):
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page()
    for x, y, t in lines:
        pg.insert_text((x, y), t)
    f = str(tmp_path / "t.pdf")
    doc.save(f)
    return f


def test_이름과_값이_한_줄인_머리_칸은_열_이름이_아니다(tmp_path):
    # ILSHIN CARBON: PO 번호가 표 열이 아니라 묶음마다 「PO NO :」 머리 칸에 찍힌다
    f = _pdf(tmp_path, [(50, 80, "PO NO :4302056146"), (50, 100, "4302056974"), (200, 100, "GRAPHITE")])
    assert L.find_header([f], "4302056974") is None


def test_다른_품목_줄은_건너뛰고_가운데_찍힌_머리글을_찾는다(tmp_path):
    # FLUSA4221: 값은 왼쪽에 붙고 머리글 ITEM CODE 는 가운데 찍힘. 위의 P3154163 은 다른 품목의 같은 칸
    f = _pdf(tmp_path, [(80, 80, "ITEM CODE"), (50, 100, "P3154163"), (50, 120, "4352403785 SEAL"),
                        (50, 300, "BISV")])
    assert L.find_header([f], "BISV") == "ITEM CODE"


def test_그_묶음에만_있는_품명으로는_코드_후보를_만들지_않는다():
    for desc in ("200MM WAFER (QJM0069H3 2EA)", "200MM WAFER (ZUB0001 #24,25)",
                 "Bureau of Industry and Security Verification CR 02/12/2026"):
        assert L.one_off_desc(desc)
        c = {"col": "Material", "orig": {"_mat_key": "sap_code"}, "before": "", "after": "R1436438", "desc": desc, "part": ""}
        assert L.suggest(c, {"pdfs": []}) == []
    assert not L.one_off_desc("PUMP SEAL KIT 300MM") and not L.one_off_desc("O-RING AS568-214")
    c = {"col": "Material", "orig": {"_mat_key": "sap_code"}, "before": "", "after": "P2165141",
         "desc": "WAFER (2EA)", "part": "4400364"}
    assert L.suggest(c, {"pdfs": []}) == [("code", {"on": "part", "key": "4400364", "field": "sap_code",
                                                    "value": "P2165141", "mode": "fill"})]


def test_다시_확정하면_그_전_확정의_후보가_걷힌다():
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    row = {"Item": "00010", "Purchase Order": "45", "Material": "", "Vendor Part No.": "", "B/L quantity": "1",
           "품명": "", "단위": "EA", "원산지": "", "Sap Code": "", "Part No.": ""}
    orig = [dict(row, **{"Vendor Part No.": "VP-1", "Part No.": "VP-1", "품명": "PUMP SEAL"}), dict(row, 품명="BLADE")]
    job = job_of(orig, sid)
    me = job["id"][:12]
    other = R.upsert_candidate("code", sid, {"on": "part", "key": "VP-1", "field": "sap_code", "value": "PKG"},
                               {"job": "bbbbbbbbbbbb", "row": 0})
    kept = R.add("code", sid, {"on": "desc", "key": "PUMP SEAL", "field": "sap_code", "value": "OLD"},
                 evidence=[{"job": me, "row": 0}])
    L.confirm(job, [dict(orig[0], _i=0, Material="PKG"), dict(orig[1], _i=1, Material="WRONG")])
    assert len(R.get(other["id"])["evidence"]) == 2
    wrong = R.find_same("code", sid, {"on": "desc", "key": "BLADE", "field": "sap_code", "value": "WRONG"})
    assert wrong["status"] == "candidate"
    L.confirm(job, [dict(orig[0], _i=0, Material="PKG"), dict(orig[1], _i=1, Material="WRONG")])  # 같은 값으로 다시 확정
    assert R.get(wrong["id"])["status"] == "candidate" and len(R.get(wrong["id"])["evidence"]) == 1
    assert not any(h["event"] == "delete" for h in R.history())                     # 지웠다 새로 만들지 않음
    L.confirm(job, [dict(orig[0], _i=0, Material="PKG"), dict(orig[1], _i=1)])      # 첫 확정이 틀려 BLADE 를 되돌림
    assert R.get(wrong["id"])["status"] == "deleted"                                # 이 작업에만 있던 후보는 지움
    assert len(R.get(other["id"])["evidence"]) == 2                                 # 다시 나온 수정은 다시 근거가 됨
    L.confirm(job, [dict(orig[0], _i=0), dict(orig[1], _i=1)])                      # AI 값 그대로 확정
    assert [e["job"] for e in R.get(other["id"])["evidence"]] == ["bbbbbbbbbbbb"]   # 다른 작업의 근거는 남김
    assert R.get(other["id"])["status"] == "candidate"
    assert R.get(kept["id"])["status"] == "active" and R.get(kept["id"])["evidence"]  # 반영한 규칙은 그대로
    assert any(h["event"] == "delete" and h.get("rule") == wrong["id"] for h in R.history())


def test_확정은_정답_자료를_남긴다(home):
    job = job_of([{"Material": "A", "품명": "X"}])
    L.confirm(job, [{"Material": "A", "품명": "X", "_i": 0}])
    assert job["confirmed"] and os.path.isfile(os.path.join(wuxi_paths.WORK, "jobs", "a" * 32, "golden.json"))


def test_서류_글에_찍힌_받는_쪽이_공급사로_잡히면_판독한_송하인으로_바로잡는다():
    """10월 512건 중 210건이 받는 쪽 「SK HYNIX SEMICONDUCTOR (CHINA) LTD.」 로 묶였다(송하인은 제대로 읽혔음, 11/15)."""
    buyer = R.save_supplier("SK HYNIX SEMICONDUCTOR (CHINA) LTD.", source="auto")["id"]
    text = "COMMERCIAL INVOICE\nSOULBRAIN CO.,LTD.\nCONSIGNEE: SK HYNIX SEMICONDUCTOR (CHINA) LTD.\nWUXI"
    pre = R.identify_supplier(text)
    assert pre == buyer                                               # 글로만 고르면 받는 쪽이 이긴다
    ex = {"shipper_name": "SOULBRAIN CO.,LTD.", "consignee_name": "SK HYNIX SEMICONDUCTOR (CHINA) LTD."}
    sid = R.supplier_after_read(pre, text, ex)
    assert sid != buyer and R.supplier_name(sid) == "SOULBRAIN CO.,LTD."
    assert R.supplier_after_read(pre, text, ex) == sid                # 두 번째부터는 있는 공급사를 쓴다


def test_송하인과_같은_공급사는_문장_부호가_달라도_찾는다():
    sid = R.save_supplier("HAPPYNARAE CO.,LTD")["id"]
    assert R.supplier_after_read(sid, "", {"shipper_name": "HAPPYNARAE CO,.LTD"}) == sid
    assert len(R.suppliers()) == 1


def test_송하인을_모르면_글로_고른_공급사를_그대로_둔다():
    """글로 고른 공급사가 받는 쪽이 아니면 함부로 새 공급사를 만들지 않는다(송하인 자리에 운송사를 읽는 일이 있다)."""
    sid = R.save_supplier("TEMC CO., LTD.")["id"]
    ex = {"shipper_name": "FAST LOGISTICS", "consignee_name": "SK HYNIX SEMICONDUCTOR (CHINA) LTD."}
    assert R.supplier_after_read(sid, "", ex) == sid
    assert len(R.suppliers()) == 1


def test_꺼_둔_검사도_결과를_남겨_다시_켜면_판정이_돌아온다():
    """운영 화면에서 검사를 켜고 끄면 다시 읽지 않고 판정만 다시 매긴다(wuxi_engine.rejudge). 느슨하게 만드는 곳이 아니다."""
    ex = ex_of()
    ex["pl_lines"] = [{"seq": 1}]
    pol = R.check_policy(None)
    pol["lines_pl"]["enabled"] = False
    c = E.completeness(ex, [], pol)
    pl = {x["check"]: x for x in c["checks"]}["lines_pl"]
    assert c["verdict"] == "이상 없음" and pl["state"] == "off" and pl["raw"] == "bad"
    on = E.rejudge(c, R.check_policy(None))
    assert on["verdict"] == "빠진 줄 의심" and on["notes"] == ["인보이스 2줄, 포장명세서 1줄로 줄 수가 다릅니다"]
    assert E.rejudge(on, pol)["verdict"] == "이상 없음"


def test_예전에_꺼_둔_채로_읽은_검사를_켜면_근거_없음으로_둔다():
    old = {"verdict": "이상 없음", "checks": [{"check": "amount", "name": "금액 합계", "state": "ok", "note": "맞음"},
                                             {"check": "lines_pl", "name": "포장명세서 줄 수", "state": "off", "note": ""}]}
    c = E.rejudge(old, R.check_policy(None))
    pl = {x["check"]: x for x in c["checks"]}["lines_pl"]
    assert pl["state"] == "skip" and "다시 읽으면" in pl["note"] and c["verdict"] == "이상 없음"


def test_검사를_끄면_확정하지_않은_작업의_판정과_자동_통과를_다시_매긴다(monkeypatch):
    import cell_check
    import wuxi_web as web
    store = web.Store()
    monkeypatch.setattr(web, "STORE", store)
    monkeypatch.setattr(cell_check, "triage", lambda items, v: {"auto": v == "이상 없음", "why": [] if v == "이상 없음" else [v]})
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    ex = ex_of()
    ex["pl_lines"] = [{"seq": 1}]
    comp = E.completeness(ex, [], R.check_policy(sid))
    jobs = {}
    for k, confirmed in (("a", False), ("b", True)):
        j = {"id": k * 32, "status": "done", "supplier": sid, "confirmed": confirmed or None,
             "payload": {"items": [], "completeness": json.loads(json.dumps(comp)), "cellcheck_v": cell_check.VERSION,
                         "triage": {"auto": False, "why": ["빠진 줄 의심"]}}}
        store.jobs[j["id"]] = jobs[k] = j
    assert web.rejudge_all() == 0                                       # 설정이 그대로면 아무것도 쓰지 않는다
    R.add("check", sid, {"check": "lines_pl", "enabled": False})
    assert web.rejudge_all() == 1
    a = jobs["a"]["payload"]
    assert a["completeness"]["verdict"] == "이상 없음" and a["triage"]["auto"] is True
    assert json.loads((web.jobs_root() / ("a" * 32) / "job.json").read_text(encoding="utf-8"))["payload"]["triage"]["auto"]
    assert jobs["b"]["payload"]["completeness"]["verdict"] == "빠진 줄 의심"   # 확정한 작업은 건드리지 않는다
    assert web.rejudge_all() == 0


def test_PO_마다_나눠_적은_수량은_빠짐_검사_뒤에_PO_마다_한_줄로_나눈다():
    """동진 「PHOTORESIST DJHS-1410 24 GAL (4303544462 : 16) (4303565585 : 8)」(10월 PNS26105010). G-ERP 는 PO 마다 한 행."""
    ex = {"ci_lines": [{"seq": 1, "description": "PHOTORESIST DJHS-1410", "qty": 24, "unit_price": 1337.0, "amount": 32088.0,
                        "po_no": "4303544462", "po_item_no": "00010",
                        "po_split": [{"po_no": "4303544462", "qty": 16}, {"po_no": "4303565585", "qty": 8}]},
                       {"seq": 2, "description": "PHOTORESIST ADJHS-2045", "qty": 16, "unit_price": 1594.0, "amount": 25504.0,
                        "po_split": []}],
          "pl_lines": [{"seq": 1}, {"seq": 2}], "invoice_totals": [{"invoice_no": "", "total": 57592.0}]}
    comp = E.completeness(ex, [], R.check_policy(None))
    assert {c["check"]: c["state"] for c in comp["checks"]}["lines_pl"] == "ok"      # 검사는 인쇄된 2줄로
    assert E.split_by_po(ex) == 1
    got = [(x.get("po_no"), x["qty"], x["amount"], x.get("po_item_no")) for x in ex["ci_lines"]]
    assert got == [("4303544462", 16, 21392.0, "00010"), ("4303565585", 8, 10696.0, None), (None, 16, 25504.0, None)]
    assert ex["ci_lines"][2]["description"] == "PHOTORESIST ADJHS-2045" and "po_split" not in ex["ci_lines"][0]


def test_나눈_수량의_합이_줄_수량과_다르면_나누지_않는다():
    ex = {"ci_lines": [{"qty": 24, "unit_price": 1.0, "po_split": [{"po_no": "A", "qty": 16}, {"po_no": "B", "qty": 9}]},
                       {"qty": 5, "po_split": [{"po_no": "A", "qty": 5}]}]}
    assert E.split_by_po(ex) == 0 and len(ex["ci_lines"]) == 2


def test_줄_밖에_적힌_PO_모델_수량으로도_나눈다():
    """와이씨켐 「Customer P/O No. : 4303517252 (YHS-140  32 GAL)」(10월 HDLCA126692, 사람이 확정한 정답과 같음)."""
    text = ("Customer P/O No. :\n4303517252 (YHS-140  32 GAL)\n4303544657 (YHS-140  190 GAL)\n"
            "4303517252 (AYCHS-1320  1 GAL)\n4303544657 (AYCHS-1320  5 GAL)\n4303517252 (YHS-140  32 GAL)\n")
    ex = {"ci_lines": [{"description": "SOC. PR (YHS-140)", "qty": 222, "unit_price": 995.0, "po_no": "4303517252"},
                       {"description": "SOC. PR(AYCHS-1320)", "qty": 6, "unit_price": 1177.0, "po_no": "4303517252"}]}
    assert E.split_by_po(ex, text) == 2
    assert [(x["po_no"], x["qty"], x["amount"]) for x in ex["ci_lines"]] == [
        ("4303517252", 32, 31840.0), ("4303544657", 190, 189050.0), ("4303517252", 1, 1177.0), ("4303544657", 5, 5885.0)]


def tok_ex():
    """10월 HDLCA126804. 사람이 확정한 정답: TOKN-4030 96(60806727), 144(60907801), TOTS-0180 96(60843428)."""
    return {"ci_lines": [{"description": "TOKN-4030 2.2CP", "qty": 240, "unit_price": 72400.0, "po_no": "4303544602"},
                         {"description": "TOTS-0180 1.42CP", "qty": 96, "unit_price": 218400.0, "po_no": "4303544602"}],
            "pl_lines": [{"description": "TOKN-4030 2.2CP (LOT: 60806727)", "qty": 96},
                         {"description": "TOKN-4030 2.2CP (LOT: 60907801)", "qty": 144},
                         {"description": "TOTS-0180 1.42CP (LOT: 60843428)", "qty": 96}]}


def test_LOT_마다_나누기_규칙이_켜진_공급사만_LOT_마다_나누고_Material_에_LOT_번호를_넣는다():
    import gerp_xlsx
    sid = R.save_supplier("TOK ADVANCED MATERIALS CO., LTD.")["id"]
    assert E.lot_rule(sid) is None
    rid = R.add("guide", sid, {"text": "In pl_lines keep the printed LOT number in description.", "action": "lot_split"})["id"]
    assert E.lot_rule(sid) == rid and E.lot_rule(R.save_supplier("ACME PARTS LTD")["id"]) is None
    ex = tok_ex()
    assert E.split_by_lot(ex, rid) == 3
    assert [(x["material_code"], x["qty"], x["amount"]) for x in ex["ci_lines"]] == [
        ("60806727", 96, 6950400.0), ("60907801", 144, 10425600.0), ("60843428", 96, 20966400.0)]
    assert gerp_xlsx.hynix_material(ex["ci_lines"][0])[0] == "60806727"     # 하이닉스 형식이 아니어도 규칙 값이라 쓴다


def test_LOT_수량_합이_인보이스와_다르면_나누지_않는다():
    ex = tok_ex()
    ex["pl_lines"][1]["qty"] = 100
    assert E.split_by_lot(ex, "r") == 1                   # TOTS 줄만(합이 맞음). TOKN 은 그대로
    assert ex["ci_lines"][0]["qty"] == 240 and "material_code" not in ex["ci_lines"][0]


def test_PO_마다_나뉜_줄은_LOT_차례대로_수량을_채운다():
    ex = {"ci_lines": [{"description": "TOKN-4030 2.2CP", "qty": 164, "unit_price": 1.0, "po_no": "A"},
                       {"description": "TOKN-4030 2.2CP", "qty": 164, "unit_price": 1.0, "po_no": "B"}],
          "pl_lines": [{"description": "TOKN-4030 2.2CP (LOT: 60908904)", "qty": 144},
                       {"description": "TOKN-4030 2.2CP (LOT: 60908904)", "qty": 20},
                       {"description": "TOKN-4030 2.2CP (LOT: 60908909)", "qty": 20},
                       {"description": "TOKN-4030 2.2CP (LOT: 60908909)", "qty": 144}]}
    assert E.split_by_lot(ex, "r") == 4
    assert [(x["po_no"], x["material_code"], x["qty"]) for x in ex["ci_lines"]] == [
        ("A", "60908904", 144), ("A", "60908904", 20), ("B", "60908909", 20), ("B", "60908909", 144)]


def test_LOT_으로_나눈_줄과_포장명세서_줄_수를_견준다():
    """TOK 포장명세서는 LOT 마다 한 줄이라 인쇄된 인보이스 줄(2줄)과 견주면 늘 「빠진 줄 의심」 이 됐다(HDLCA126805)."""
    ex = tok_ex()
    assert {c["check"]: c["state"] for c in E.completeness(ex, [], R.check_policy(None))["checks"]}["lines_pl"] == "bad"
    assert E.lot_covers(ex)                              # 품명마다 LOT 수량 합이 맞아 줄 수 견주기를 갈음한다
    ex["pl_lines"].append({"description": "TARF-TAI-1006 1.4CP (LOT: 60906709)", "qty": 24})
    assert not E.lot_covers(ex)                          # 인보이스에 없는 LOT 줄이 있으면 갈음하지 않는다
