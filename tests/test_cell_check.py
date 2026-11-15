"""칸별 원문 대조(tools/cell_check.py). 가짜 인보이스 PDF 를 그 자리에서 만들어 본다. AI 를 부르지 않는다."""
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

import cell_check as C  # noqa: E402

HEAD = "COMMERCIAL INVOICE   Invoice No. INV-1   Currency USD   Payment terms T/T 30 days " * 2


def doc_with(lines, head=HEAD):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((40, 40), head[:90], fontsize=8)
    p.insert_text((40, 52), head[90:180], fontsize=8)
    y = 90
    for ln in lines:
        p.insert_text((40, y), ln, fontsize=9)
        y += 30
    return d


def row(**kw):
    base = {"출처": "CI", "Purchase Order": "4303172339", "Material": "P3024498", "품명": "PUMP SEAL KIT",
            "Vendor Part No.": "", "B/L quantity": 10, "단위": "EA", "Net Price": 12.5, "Currency": "USD"}
    base.update(kw)
    return base


META = [{"file": "a.pdf", "no": 1, "kind": "CI", "scan": True}]     # 로고만 있어도 scan 이 켜진다. 글자층으로 판단해야 한다


def check(items, lines):
    d = doc_with(lines)
    try:
        return C.check_items(items, [("a.pdf", d)], META)
    finally:
        d.close()


def test_원본에_있는_값은_확인되고_틀린_수량은_못_찾음이다():
    items = [row(), row(**{"Material": "P3024499", "품명": "O-RING SET", "B/L quantity": 3, "Net Price": 1.25})]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00",
                  "2  4303172339  P3024499  O-RING SET       5  EA   1.25    6.25"])
    assert items[0]["_ok"] == {c: "ok" for c in C.CHECK_COLS if c != "Vendor Part No."} | {"Vendor Part No.": "-"}
    assert items[1]["_ok"]["B/L quantity"] == "miss"       # 원본은 5, 판독은 3
    assert items[1]["_ok"]["Net Price"] == "ok"


def test_같은_코드가_여러_줄이면_수량과_단가가_맞는_줄을_잡는다():
    """같은 Material 이 두 줄에 찍혀도 각 줄의 숫자를 그 줄에서 찾는다."""
    items = [row(**{"B/L quantity": 2, "Net Price": 7}), row(**{"B/L quantity": 9, "Net Price": 8})]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT   2  EA  7.00   14.00",
                  "   (spacer line one)", "   (spacer line two)", "   (spacer line three)", "   (spacer line four)",
                  "2  4303172339  P3024498  PUMP SEAL KIT   9  EA  8.00   72.00"])
    assert [r["_ok"]["B/L quantity"] for r in items] == ["ok", "ok"]


def test_빈_Material_칸에_원본_같은_줄의_하이닉스_번호를_알린다():
    """PNS26090853: Remark 칸의 R1436438 을 AI 가 못 읽었다."""
    items = [row(Material="", **{"품명": "200MM WAFER", "B/L quantity": 2, "Net Price": 300})]
    check(items, ["1  4303172339  200MM WAFER   2  EA  300.00   600   KOREA   R1436438"])
    assert items[0]["_ok"]["Material"] == "-"
    assert items[0]["_hint"]["Material"] == "R1436438"


def test_스캔이라_글자층이_없으면_확인_불가다():
    """OCR 을 끄거나 OCR 엔진이 없으면 예전처럼 견주지 않는다."""
    d = pymupdf.open()
    d.new_page()
    items = [row()]
    try:
        C.check_items(items, [("a.pdf", d)], META, ocr=False)
    finally:
        d.close()
    assert set(items[0]["_ok"].values()) <= {"?", "-"}


def ocr_lines(lines, y=90):
    """가짜 OCR 결과. 줄마다 [x0, y0, x1, y1, 글]."""
    out = [[40, 40, 400, 50, HEAD[:90]]]
    for ln in lines:
        out.append([40, y - 9, 40 + 5 * len(ln), y, ln])
        y += 30
    return out


def test_스캔_쪽은_OCR_글자로_견준다(monkeypatch):
    """10월 50건 중 29건이 스캔이라 칸을 하나도 견주지 못하고 모두 사람에게 넘어갔다(11/14)."""
    got = ocr_lines(["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00",
                     "2  4303172339  P3024499  O-RING SET       5  EA   1.25    6.25"])
    monkeypatch.setattr(C, "ocr_page", lambda page: got)
    monkeypatch.setattr(C, "_engine", lambda: True)
    d = pymupdf.open()
    d.new_page()
    items = [row(), row(**{"Material": "P3024499", "품명": "O-RING SET", "B/L quantity": 3, "Net Price": 1.25})]
    try:
        C.check_items(items, [("a.pdf", d)], META)
    finally:
        d.close()
    assert items[0]["_ok"] == {c: "ok" for c in C.CHECK_COLS if c != "Vendor Part No."} | {"Vendor Part No.": "-"}
    assert items[1]["_ok"]["B/L quantity"] == "miss"       # 원본은 5, 판독은 3
    assert items[0]["_at"]["Material"][:2] == [0, 1]        # 원본 위 상자도 OCR 자리로 그린다
    assert C.triage(items[:1], "이상 없음")["auto"] is True


def test_OCR_은_PDF_옆에_남겨_한_번만_한다(tmp_path, monkeypatch):
    calls = []

    def fake(page):
        calls.append(1)
        return ocr_lines(["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00"])
    monkeypatch.setattr(C, "ocr_page", fake)
    monkeypatch.setattr(C, "_engine", lambda: True)
    f = tmp_path / "a.pdf"
    d = pymupdf.open()
    d.new_page()
    d.save(str(f))
    d.close()
    for _ in range(2):
        items = [row()]
        d = pymupdf.open(str(f))
        try:
            C.check_items(items, [("a.pdf", d)], META)
        finally:
            d.close()
        assert items[0]["_ok"]["Material"] == "ok"
    assert len(calls) == 1
    assert (tmp_path / "a.pdf.ocr.json").is_file()


def test_단가_0_도_빈_칸이_아니라_원본과_견준다():
    """무상 품목의 단가 0 을 빈 칸으로 보면 원본이 10,729.14 여도 넘어간다."""
    items = [row(**{"Net Price": 0}), row(**{"Material": "P3024499", "품명": "O-RING SET", "B/L quantity": 5, "Net Price": 0})]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  0.00  0.00",
                  "   (spacer line one)", "   (spacer line two)", "   (spacer line three)", "   (spacer line four)",
                  "2  4303172339  P3024499  O-RING SET       5  EA   10,729.14    53,645.70"])
    assert items[0]["_ok"]["Net Price"] == "ok"
    assert items[1]["_ok"]["Net Price"] == "miss"


def test_작업을_열면_대조를_붙이고_확정_여부를_같이_낸다(tmp_path, monkeypatch):
    """원문 대조 전에 읽은 작업은 처음 열 때 대조한다. confirmed 가 빠지면 다시 연 화면에 「확정함」이 안 보인다."""
    from fastapi.testclient import TestClient

    import wuxi_paths
    import wuxi_web
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    monkeypatch.setattr(wuxi_web, "_authed", lambda request: True)
    wuxi_web.STORE.jobs.clear()
    wuxi_web.STORE.loaded = True
    jid = "f" * 32
    d = wuxi_web.STORE.dir(jid)
    d.mkdir(parents=True)
    pdf = doc_with(["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00"])
    pdf.save(str(d / "00_a.pdf"))
    pdf.close()
    job = {"id": jid, "status": "done", "created": 1, "names": ["00_a.pdf"], "pdfs": [str(d / "00_a.pdf")],
           "confirmed": 123.0, "review": {"result": "ok"},
           "payload": {"items": [row()], "pages": [{"file": "00_a.pdf", "no": 1, "kind": "CI"}]}}
    wuxi_web.STORE.jobs[jid] = job
    got = TestClient(wuxi_web.app).get(f"/api/jobs/{jid}").json()
    assert got["confirmed"] == 123.0 and got["review"] == {"result": "ok"}
    assert got["payload"]["cellcheck"]["ok"] >= 6
    assert got["payload"]["items"][0]["_ok"]["Material"] == "ok"


def test_통화는_달러_표시로도_확인한다():
    items = [row()]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  $12.50  $125.00"])
    assert items[0]["_ok"]["Currency"] == "ok"


def test_칸마다_그_줄의_원본_자리를_낸다():
    """COKR26015156: 금액이 원본에 쉼표를 넣어 찍혀 예전에는 상자가 하나도 안 나왔다. 줄마다 PO 가 같아도 각 줄 자리를 가리킨다."""
    items = [row(**{"Invoice amount": 1250000, "B/L quantity": 100000, "Net Price": 12.5}),
             row(**{"Material": "P3024499", "품명": "O-RING SET", "B/L quantity": 5, "Net Price": 1.25, "Invoice amount": 6.25})]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT   100,000  EA  12.50  1,250,000.00",
                  "   (spacer line one)", "   (spacer line two)", "   (spacer line three)", "   (spacer line four)",
                  "2  4303172339  P3024499  O-RING SET       5  EA   1.25    6.25"])
    a, b = items[0]["_at"], items[1]["_at"]
    assert set(a) == {"Purchase Order", "Material", "품명", "B/L quantity", "단위", "Net Price", "Invoice amount"}
    assert a["Invoice amount"][:2] == [0, 1] and a["Invoice amount"][2] > a["Net Price"][2]
    assert abs(a["Purchase Order"][3] - a["Material"][3]) < 2           # 같은 줄 높이
    assert b["Purchase Order"][3] > a["Purchase Order"][3] + 100        # 둘째 줄은 아래쪽 자기 줄의 PO
    assert items[0]["_hl"] and all(x[:2] == [0, 1] for x in items[0]["_hl"])


def test_다른_서류에만_있는_값은_가리키지_않는다():
    """운송장에만 찍힌 PO 를 인보이스 줄의 자리로 그리지 않는다."""
    awb, ci = pymupdf.open(), doc_with(["1  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00"])
    awb.new_page().insert_text((40, 90), "BILL OF LADING  PO NO. 4303172339  " * 3, fontsize=8)
    meta = [{"file": "b.pdf", "no": 1, "kind": "AWB"}, {"file": "a.pdf", "no": 1, "kind": "CI"}]
    items = [row()]
    try:
        C.check_items(items, [("b.pdf", awb), ("a.pdf", ci)], meta)
    finally:
        awb.close(), ci.close()
    assert "Purchase Order" not in items[0]["_at"]
    assert items[0]["_at"]["Material"][:2] == [1, 1]


def _flagged(**kw):
    r = row(**kw)
    r["_ok"] = {c: ("ok" if str(r.get(c) or "").strip() else "-") for c in C.CHECK_COLS}
    r["_hint"] = {}
    return r


def test_확인할_칸이_없고_빠진_줄이_없으면_자동_통과다():
    assert C.triage([_flagged()], "이상 없음") == {"auto": True, "why": [], "flags": 0}


def test_빈_PO_못_찾은_칸_스캔_빠진_줄_의심이면_사람이_본다():
    r = _flagged(**{"Purchase Order": ""})
    t = C.triage([r], "이상 없음")
    assert t["auto"] is False and t["why"] == ["확인할 칸 1개"]
    r = _flagged(); r["_ok"]["B/L quantity"] = "miss"
    assert C.triage([r], "이상 없음")["why"] == ["확인할 칸 1개"]
    r = _flagged(); r["_ok"]["품명"] = "?"
    assert C.triage([r], "이상 없음")["why"] == ["스캔 서류"]
    assert C.triage([_flagged()], "빠진 줄 의심")["why"] == ["빠진 줄 의심"]
    assert C.triage([_flagged()], "확인 못 함")["why"] == ["빠진 줄 확인 못 함"]


def test_인보이스_줄이_있으면_PL_에만_있는_줄은_자동_통과를_막지_않는다():
    """화면이 그런 줄을 표에서 빼므로(정답 93건에서 209줄 중 208줄을 사람이 지움) 판정에서도 뺀다."""
    pl = _flagged(**{"출처": "PL", "Material": "", "Purchase Order": ""})
    assert C.triage([_flagged(), pl], "이상 없음")["auto"] is True
    assert C.triage([pl], "이상 없음")["why"] == ["인보이스 줄을 못 읽음", "확인할 칸 2개"]


def test_빈_부품_번호에_그_줄의_일련번호가_있으면_사람이_본다():
    """HDLCA125295: 사람이 빈 Vendor Part No. 에 일련번호를 넣었다. 자동 통과로 넘기지 않는다."""
    items = [row(**{"Serial No.": "125XH001301"})]
    check(items, ["1  4303172339  P3024498  PUMP SEAL KIT  125XH001301  10  EA  12.50  125.00"])
    assert items[0]["_hint"]["Vendor Part No."] == "125XH001301"
    assert C.triage(items, "이상 없음")["auto"] is False


def test_스캔_쪽_여럿은_엔진_OCR_일꾼으로_한꺼번에_읽어_둔다(tmp_path, monkeypatch):
    """스캔 6쪽짜리 건에서 쪽을 하나씩 OCR 하느라 대조에만 3분이 들었다(11/15)."""
    asked = []

    def pool(pgs):
        asked.append(len(pgs))
        return [ocr_lines(["1  4303172339  P3024498  PUMP SEAL KIT   10  EA  12.50  125.00"]),
                ocr_lines(["2  4303172339  P3024499  O-RING SET       5  EA   1.25    6.25"])]

    def one(page):
        raise AssertionError("미리 읽어 둔 쪽을 다시 OCR 하면 안 됩니다")
    monkeypatch.setattr(C, "_pool_ocr", pool)
    monkeypatch.setattr(C, "ocr_page", one)
    monkeypatch.setattr(C, "_engine", lambda: True)
    f = tmp_path / "a.pdf"
    d = pymupdf.open()
    d.new_page()
    d.new_page()
    d.save(str(f))
    d.close()
    meta = META + [{"file": "a.pdf", "no": 2, "kind": "CI", "scan": True}]
    items = [row(), row(**{"Material": "P3024499", "품명": "O-RING SET", "B/L quantity": 5, "Net Price": 1.25})]
    d = pymupdf.open(str(f))
    try:
        C.check_items(items, [("a.pdf", d)], meta)
    finally:
        d.close()
    assert asked == [2]
    assert items[0]["_ok"]["Material"] == "ok" and items[1]["_ok"]["B/L quantity"] == "ok"
    assert items[1]["_at"]["Material"][:2] == [0, 2]                 # 둘째 쪽의 자리
    cache = json.loads((tmp_path / "a.pdf.ocr.json").read_text(encoding="utf-8"))
    assert sorted(cache["pages"]) == ["1", "2"]                       # 다시 대조할 때는 OCR 하지 않는다
