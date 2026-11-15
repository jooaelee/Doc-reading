"""스캔 쪽 OCR 을 한 건에 한 번만 하는지(11/15). 화면이 공급사를 알아보려고 가른 쪽을 판독(docpipe.run)이 그대로 쓴다."""
import os
import sys

import pymupdf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import docpipe  # noqa: E402
import wuxi_engine  # noqa: E402


def _scan_pdf(path):
    """글자층이 없는 쪽 하나(스캔처럼 그림만)."""
    doc = pymupdf.open()
    pg = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.set_rect(pix.irect, (128, 128, 128))
    pg.insert_image(pymupdf.Rect(50, 50, 450, 250), pixmap=pix)
    doc.save(path)
    doc.close()


def test_판독은_받은_쪽을_쓰고_다시_가르거나_OCR_하지_않는다(tmp_path, monkeypatch):
    pdf = str(tmp_path / "a.pdf")
    _scan_pdf(pdf)
    pages = docpipe.apply_awb_sort([docpipe.classify(p) for p in docpipe.load_pdf(pdf)])
    assert [p.kind for p in pages] == ["SCAN"]

    def no(*a, **k):
        raise AssertionError("다시 가르거나 OCR 하면 안 됩니다")
    monkeypatch.setattr(docpipe, "load_pdf", no)
    monkeypatch.setattr(docpipe, "ocr_pages", no)
    monkeypatch.setattr(docpipe, "route_scans", no)
    rows = docpipe.run([("x", [pdf])], None, show=False, prepared={"x": pages})
    assert rows[0]["pages"] == len(pages)


class _Stop(Exception):
    pass


def test_화면과_판독이_OCR_을_한_번만_하고_그림은_판독이_끝난_뒤_지운다(tmp_path, monkeypatch):
    pdf = str(tmp_path / "a.pdf")
    _scan_pdf(pdf)
    calls, seen = [], {}
    real = docpipe.ocr_pages

    def ocr_once(pages):
        calls.append(len(pages))
        d = str(tmp_path / f"ocr{len(calls)}")
        os.makedirs(d)
        for p in pages:
            if p.kind == "SCAN":
                p.png = os.path.join(d, "p.png"); p.ocr = ""
        return d
    monkeypatch.setattr(docpipe, "ocr_pages", ocr_once)

    def fake_run(bundles, codes, **k):
        prep = k.get("prepared") or {}
        seen["prepared"] = prep
        seen["png_alive"] = all(os.path.exists(os.path.dirname(p.png)) for ps in prep.values() for p in ps if getattr(p, "png", None))
        raise _Stop()
    monkeypatch.setattr(docpipe, "run", fake_run)
    with pytest.raises(_Stop):
        wuxi_engine.read_bundle([pdf], name="x")
    assert calls == [1]                                   # 화면에서 한 번
    assert list(seen["prepared"]) == ["x"] and len(seen["prepared"]["x"]) == 1
    assert seen["png_alive"]                              # 판독이 그림을 그대로 모델에 보낸다
    assert not os.path.exists(str(tmp_path / "ocr1"))     # 끝나면(실패해도) 지운다
    assert real is not ocr_once


def test_효율_모드를_끄는_표시는_실패해도_조용히_넘어간다(monkeypatch):
    monkeypatch.setenv("DOCPIPE_NO_THROTTLE", "0")
    assert docpipe.no_power_throttle() is False
    monkeypatch.setenv("DOCPIPE_NO_THROTTLE", "1")
    assert docpipe.no_power_throttle() in (True, False)      # Windows 가 아니면 False
