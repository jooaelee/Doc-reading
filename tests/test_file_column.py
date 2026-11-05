"""「파일명」 열. 줄마다 읽은 PDF 이름을 적어 앱 밖에서 원본과 대조하게 한다. AI 를 부르지 않는다."""
import io

import openpyxl
import pymupdf
import pytest
from fastapi.testclient import TestClient

import gerp_xlsx
import wuxi_engine
import wuxi_paths
import wuxi_rules as R


def _pdf(path, text):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_열은_엑셀과_화면_열_목록에_있다():
    assert "파일명" in gerp_xlsx.EXTRA_ITEM_COLS and "파일명" in gerp_xlsx.ITEM_COLS
    assert "파일명" not in gerp_xlsx.GERP_ITEM_COLS          # G-ERP 열 순서는 그대로
    assert any(r[1] == "파일명" for r in gerp_xlsx.ORIGIN)   # 「읽는 법」 시트에 설명이 있다


def test_파일을_고르는_차례():
    kinds = {(0, 1): "CI", (1, 1): "PL", (2, 1): "CI"}
    f = ["A.pdf", "B.pdf", "C.pdf"]
    assert wuxi_engine._row_file([], ["only.pdf"], {}) == "only.pdf"        # 하나면 찾지 않아도 그 파일
    assert wuxi_engine._row_file([[0, 1, 0, 0, 1, 1], [1, 1, 0, 0, 1, 1]], f, kinds) == "A.pdf"   # 인보이스 쪽이 먼저
    assert wuxi_engine._row_file([[1, 1, 0, 0, 1, 1]], f, kinds) == "B.pdf"   # 인보이스 쪽에 없으면 찾은 파일
    assert wuxi_engine._row_file([[0, 1, 0, 0, 1, 1], [2, 1, 0, 0, 1, 1]], f, kinds) == "A.pdf, C.pdf"
    assert wuxi_engine._row_file([], f, kinds) == "A.pdf, B.pdf, C.pdf"       # 못 찾으면(스캔 등) 전부


def test_payload_가_줄마다_파일명을_채운다(tmp_path):
    a = _pdf(tmp_path / "00_HDLCA1.pdf", "INVOICE F149A0001 4500012345")
    b = _pdf(tmp_path / "01_HDLCA2.pdf", "INVOICE F163E0003 4500099999")
    ex = {"ci_lines": [{"po_no": "4500012345", "po_item_no": "00010", "sap_code": "F149A0001", "qty": 1, "amount": 10},
                       {"po_no": "4500099999", "po_item_no": "00010", "sap_code": "F163E0003", "qty": 1, "amount": 20}]}
    res = {"ex": ex, "verify": [], "trace": [], "completeness": {}, "kinds": ["CI", "CI"],
           "pages": [{"file": "00_HDLCA1.pdf", "no": 1, "kind": "CI"}, {"file": "01_HDLCA2.pdf", "no": 1, "kind": "CI"}]}
    p = wuxi_engine.payload("x", [a, b], res, files=["HDLCA1 & 원본.pdf", "HDLCA2.pdf"])
    assert [r["파일명"] for r in p["items"]] == ["HDLCA1 & 원본.pdf", "HDLCA2.pdf"]
    assert "파일명" in p["cols"]["extra"]
    # 원래 이름이 없으면 저장된 이름을 쓴다
    assert wuxi_engine.payload("x", [a], dict(res, pages=res["pages"][:1]))["items"][0]["파일명"] == "00_HDLCA1.pdf"


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


def test_엑셀에_파일명이_나가고_더한_줄도_채우며_학습에는_안_섞인다(env):
    web, local = env
    row = {"Item": "00010", "Purchase Order": "45", "Material": "M1", "B/L quantity": "1", "Invoice amount": "10",
           "Net Price": "10", "파일명": "HDLCA125978.pdf", "품명": "PUMP SEAL"}
    job = {"id": "c" * 32, "status": "done", "created": __import__("time").time(), "names": ["00_HDLCA125978.pdf"],
           "orig_names": ["HDLCA125978.pdf"], "pdfs": [], "steps": [],
           "payload": {"items": [row], "head": {}, "inv": [], "review": {}, "pages": [], "counts": {}, "required": [],
                       "completeness": {}, "rules": [],
                       "cols": {"gerp": gerp_xlsx.GERP_ITEM_COLS, "extra": gerp_xlsx.EXTRA_ITEM_COLS}}}
    web.STORE.jobs[job["id"]] = job
    assert local.get(f"/api/jobs/{job['id']}").json()["files"] == ["HDLCA125978.pdf"]
    added = {c: "" for c in gerp_xlsx.GERP_ITEM_COLS} | {"Purchase Order": "46", "Material": "M2", "파일명": ""}
    r = local.post(f"/api/jobs/{job['id']}/xlsx",
                   json={"items": [dict(row, _i=0, 파일명="고친 이름.pdf"), added], "deleted": []})
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.content))["BL Item"]
    head = [c.value for c in ws[1]]
    col = head.index("파일명")
    assert [ws.cell(row=i, column=col + 1).value for i in (2, 3)] == ["고친 이름.pdf", "HDLCA125978.pdf"]
    # 파일명이 바뀐 것은 학습 신호가 아니다. 더한 줄은 줄 추가로만 남는다
    assert job["edits"]["cells"] == 0 and job["edits"]["added"] == 1
