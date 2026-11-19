"""「BL No.」 열. 서류에서 읽지 않고 올린 PDF 파일 이름(우시는 HBL 번호)에서 넣는다. AI 를 부르지 않는다."""
import io

import openpyxl
import pymupdf
import pytest
from fastapi.testclient import TestClient

import accuracy
import gerp_xlsx as G
import wuxi_engine
import wuxi_paths
import wuxi_rules as R


def _pdf(path, text):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_파일_이름에서_BL_No_를_낸다():
    assert G.bl_no_of_file("HDLCA125978.pdf") == "HDLCA125978"
    assert G.bl_no_of_file("C:/x/HDLCA125978.PDF") == "HDLCA125978"
    assert G.bl_no_of_file("SFSA2610153  正式件.pdf") == "SFSA2610153"     # bulk_jobs.case_of_file 과 같은 자르기
    assert G.bl_no_of_file("") == ""


def test_항목_정의에_있고_정확도에서는_뺀다():
    spec = next(s for s in G.ITEM_SPEC if s[1] == "BL No.")
    assert spec[0] == "Main" and spec[2] == "BL No." and spec[3] == "file"
    assert "BL No." in G.EXTRA_ITEM_COLS and "BL No." not in G.GERP_ITEM_COLS    # G-ERP 열 순서는 그대로
    assert "BL No." in G.MAIN_COLS                                              # 「Main 항목」 시트에 나간다
    assert all(c[1] != "BL No." for c in accuracy.MAIN + accuracy.CHECKED)      # 판독 정확도를 부풀리지 않는다
    assert any(r[1] == "BL No." for r in G.ORIGIN)


def test_줄마다_BL_No_를_고르는_차례():
    pdfs = ["/j/00_A.pdf", "/j/01_B.pdf", "/j/02_C.pdf"]
    files = ["HBL1.pdf", "HBL2 PL.pdf", "HBL3.pdf"]
    pages = [{"file": "00_A.pdf", "no": 1, "kind": "CI"}, {"file": "01_B.pdf", "no": 1, "kind": "PL"},
             {"file": "02_C.pdf", "no": 1, "kind": "CI"}]
    rows = [{"_at": {"Material": [0, 1, 0, 0, 1, 1], "품명": [1, 1, 0, 0, 1, 1]}},  # 인보이스 쪽이 먼저
            {"_hl": [[1, 1, 0, 0, 1, 1]]},                                            # 인보이스 쪽이 없으면 찾은 파일
            {"_hl": [[0, 1, 0, 0, 1, 1], [2, 1, 0, 0, 1, 1]]},                        # 못 좁히면 건 이름
            {}]                                                                       # 자리가 없어도 건 이름
    wuxi_engine.fill_bl_no(rows, pdfs, files, pages, case="CASE9")
    assert [r["BL No."] for r in rows] == ["HBL1", "HBL2", "CASE9", "CASE9"]
    rows = [{}]
    wuxi_engine.fill_bl_no(rows, pdfs, files, pages)
    assert rows[0]["BL No."] == "HBL1, HBL2, HBL3"                                 # 건 이름도 없으면 전부
    one = [{}, {"_hl": []}]
    wuxi_engine.fill_bl_no(one, ["/j/00_x.pdf", "/j/01_y.pdf"], ["HDLCA1.pdf", "HDLCA1 CI.pdf"], [])
    assert [r["BL No."] for r in one] == ["HDLCA1", "HDLCA1"]                       # 모두 같은 BL 이면 그 값


def test_payload_가_줄마다_채운다(tmp_path):
    a = _pdf(tmp_path / "00_HDLCA1.pdf", "INVOICE F149A0001 4500012345")
    b = _pdf(tmp_path / "01_HDLCA2.pdf", "INVOICE F163E0003 4500099999")
    ex = {"ci_lines": [{"po_no": "4500012345", "po_item_no": "00010", "sap_code": "F149A0001", "qty": 1, "amount": 10},
                       {"po_no": "4500099999", "po_item_no": "00010", "sap_code": "F163E0003", "qty": 1, "amount": 20}]}
    res = {"ex": ex, "verify": [], "trace": [], "completeness": {}, "kinds": ["CI", "CI"],
           "pages": [{"file": "00_HDLCA1.pdf", "no": 1, "kind": "CI"}, {"file": "01_HDLCA2.pdf", "no": 1, "kind": "CI"}]}
    p = wuxi_engine.payload("x", [a, b], res, files=["HDLCA1.pdf", "HDLCA2 & 원본.pdf"])
    assert [r["BL No."] for r in p["items"]] == ["HDLCA1", "HDLCA2"]
    assert "BL No." in p["cols"]["extra"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    R._cache.update(key=None, state=None)
    import wuxi_web
    wuxi_web.STORE.jobs.clear()
    wuxi_web.STORE.loaded = True
    local = TestClient(wuxi_web.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000))
    local.post("/api/admin/setup", json={"admin": "admin-pass-1", "user": "user-pass-1"})
    return wuxi_web, local


def _job(web, items, extra=None):
    job = {"id": "c" * 32, "status": "done", "created": __import__("time").time(), "names": ["00_HDLCA125978.pdf"],
           "orig_names": ["HDLCA125978.pdf"], "pdfs": [], "steps": [], "cellcheck_v": None,
           "payload": {"items": items, "head": {}, "inv": [], "review": {}, "pages": [], "counts": {}, "required": [],
                       "completeness": {}, "rules": [], "cellcheck_v": "skip",
                       "cols": {"gerp": G.GERP_ITEM_COLS, "extra": extra or G.EXTRA_ITEM_COLS}}}
    web.STORE.jobs[job["id"]] = job
    return job


def test_열이_생기기_전에_읽은_작업도_열면_채운다(env, monkeypatch):
    web, local = env
    monkeypatch.setattr(web, "_cell_check", lambda job, only_stale=False: None)
    old_extra = [c for c in G.EXTRA_ITEM_COLS if c != "BL No."]
    job = _job(web, [{"Item": "00010", "Purchase Order": "45", "Material": "M1"}], extra=old_extra)
    out = local.get(f"/api/jobs/{job['id']}").json()
    assert out["payload"]["items"][0]["BL No."] == "HDLCA125978"
    assert "BL No." in out["payload"]["cols"]["extra"] and out["files"] == ["HDLCA125978.pdf"]


def test_엑셀에_나가고_더한_줄도_채우며_학습에는_안_섞인다(env):
    web, local = env
    row = {"Item": "00010", "Purchase Order": "45", "Material": "M1", "B/L quantity": "1", "Invoice amount": "10",
           "Net Price": "10", "BL No.": "HDLCA125978", "품명": "PUMP SEAL"}
    job = _job(web, [row])
    added = {c: "" for c in G.GERP_ITEM_COLS} | {"Purchase Order": "46", "Material": "M2", "BL No.": ""}
    r = local.post(f"/api/jobs/{job['id']}/xlsx", json={"items": [dict(row, _i=0, **{"BL No.": "바꾼 값"}), added], "deleted": []})
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb["BL Item"]
    col = [c.value for c in ws[1]].index("BL No.") + 1
    assert [ws.cell(row=i, column=col).value for i in (2, 3)] == ["바꾼 값", "HDLCA125978"]
    assert "BL No." in [c.value for c in wb["Main 항목"][1]]
    assert job["edits"]["cells"] == 0 and job["edits"]["added"] == 1     # BL No. 가 달라도 칸 수정이 아니다


def _pdf_bytes(text="X"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    b = doc.tobytes()
    doc.close()
    return b


def test_BL_마다_묶는다():
    import wuxi_web
    g = wuxi_web.bl_groups(["HDLCA1.pdf", "NEC2 CI.pdf", "HDLCA1 PL.pdf", "NEC2 PL.pdf", "MTOPA3.pdf"])
    assert g == [("HDLCA1", [0, 2]), ("NEC2", [1, 3]), ("MTOPA3", [4])]


def test_여러_BL_을_한_번에_올리면_BL_마다_작업이_생긴다(env, monkeypatch):
    web, local = env
    monkeypatch.setattr(web.STORE, "start", lambda: None)        # 시험에서는 판독(AI)을 돌리지 않는다
    files = [("pdf", (n, _pdf_bytes(n), "application/pdf"))
             for n in ("MTOPA2611006.pdf", "NEC51214715 CI.pdf", "NEC51214715 PL.pdf", "NEK18551621.pdf")]
    r = local.post("/api/jobs", files=files)
    assert r.status_code == 200, r.text
    made = r.json()["jobs"]
    assert [(m["bl"], m["files"]) for m in made] == [("MTOPA2611006", 1), ("NEC51214715", 2), ("NEK18551621", 1)]
    assert r.json()["job"] == made[0]["job"]
    jobs = [web.STORE.jobs[m["job"]] for m in made]
    assert [j["case"] for j in jobs] == ["MTOPA2611006", "NEC51214715", "NEK18551621"]
    assert jobs[1]["orig_names"] == ["NEC51214715 CI.pdf", "NEC51214715 PL.pdf"]
    assert jobs[0]["created"] < jobs[1]["created"] < jobs[2]["created"]         # 올린 차례대로 읽는다


def test_PDF_가_아닌_파일이_하나라도_있으면_아무_작업도_안_만든다(env, monkeypatch):
    web, local = env
    monkeypatch.setattr(web.STORE, "start", lambda: None)
    r = local.post("/api/jobs", files=[("pdf", ("A1111.pdf", _pdf_bytes(), "application/pdf")),
                                       ("pdf", ("B2222.pdf", b"not a pdf", "application/pdf"))])
    assert r.status_code == 400 and not web.STORE.jobs
