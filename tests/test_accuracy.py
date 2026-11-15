"""정확도 재기(tools/accuracy.py) 잠금. AI 를 부르지 않는다."""
import json
import os
import subprocess
import sys

import pytest

import wuxi_paths
import accuracy as A

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    yield tmp_path


def row(**kw):
    base = {"Invoice No.": "INV1", "Purchase Order": "4302056146", "Material": "P2165141", "품명": "SEAL KIT",
            "Vendor Part No.": "4400364", "B/L quantity": 2, "단위": "EA", "Net Price": 10.5, "Currency": "USD", "출처": "CI"}
    base.update(kw)
    return base


def put(home, jid, items, gold=None, case="C1", source="bulk", started=100.0, gold_time=200.0, created=1.0):
    d = home / "jobs" / jid
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"id": jid, "status": "done", "case": case, "source": source, "created": created,
                                            "started": started, "supplier_name": "ACME",
                                            "payload": {"items": items}}), encoding="utf-8")
    if gold is not None:
        (d / "golden.json").write_text(json.dumps({"time": gold_time, "items": gold}), encoding="utf-8")


def test_칸과_줄을_나눠_센다(home):
    ai = [row(), row(**{"Purchase Order": "", "품명": "BLADE", "Material": "X1", "Vendor Part No.": "VP"}),
          row(**{"출처": "PL", "Net Price": "", "B/L quantity": 1})]
    gold = [dict(ai[0], _i=0, **{"B/L quantity": "2.00"}),                        # 숫자는 숫자로 견줌
            dict(ai[1], _i=1, **{"Purchase Order": "4302056974", "Material": "", "Vendor Part No.": "VP-1"})]
    put(home, "a" * 32, ai, gold)
    m = A.measure(A.load_sets(), detail=True)
    assert m["rows"] == {"gold": 2, "read": 3, "kept": 2, "extra": 1, "missing": 0}
    assert m["cols"]["Purchase Order"]["blank"] == 1 and m["cols"]["Material"]["extra"] == 1
    assert m["cols"]["Vendor Part No."]["diff"] == 1 and m["cols"]["B/L quantity"]["ok"] == 2
    assert m["main"]["ok"] == 14 - 3 and m["main"]["n"] == 14
    assert "Invoice amount" not in m["cols"]                     # 대조 모드에서 보지 않은 Sub 칸은 세지 않음
    w = m["by_case"]["C1"]["wrong"]
    assert any(x.startswith("남는 줄 3 (PL)") for x in w) and any("PO No.: AI '', 정답 '4302056974'" in x for x in w)


def test_다시_읽으면_줄_내용으로_짝짓고_빠진_줄을_센다(home):
    gold = [dict(row(), _i=0), dict(row(**{"품명": "BLADE", "Material": "X1", "Vendor Part No.": "VP"}), _i=1),
            dict(row(**{"품명": "O-RING", "Material": "R1000001", "Vendor Part No.": "OR-9", "Net Price": 1}), _i=2)]
    put(home, "a" * 32, [row()], gold)                           # 옛 판독(확정한 작업)
    reread = [row(**{"품명": "BLADE", "Material": "X1", "Vendor Part No.": "VP", "단위": "PCS"}),   # 순서가 바뀜
              dict(row(), **{"Purchase Order": ""}),
              row(**{"품명": "CABLE", "Material": "C9", "Vendor Part No.": "CB", "Net Price": 7, "Invoice No.": "INV2",
                     "Purchase Order": "", "B/L quantity": 9})]
    put(home, "b" * 32, reread, case="C1", created=5.0)        # 같은 건을 새 작업으로 다시 읽음
    s = A.load_sets()
    assert len(s) == 1 and not s[0]["same_read"] and s[0]["job"] == "b" * 32
    m = A.measure(s)
    assert m["rows"] == {"gold": 3, "read": 3, "kept": 2, "extra": 1, "missing": 1}
    assert m["cols"]["단위"]["diff"] == 1 and m["cols"]["Purchase Order"]["blank"] == 1
    assert m["main"]["with_missing"] == pytest.approx(m["main"]["ok"] / (m["main"]["n"] + 7))
    assert m["matched_by_content"] == ["C1"]


def test_같은_작업을_다시_읽어도_내용으로_짝짓는다(home):
    gold = [dict(row(), _i=1)]                                   # 확정할 때는 둘째 줄이었다
    put(home, "a" * 32, [row()], gold, started=300.0, gold_time=200.0)
    s = A.load_sets()
    assert not s[0]["same_read"]
    assert A.measure(s)["rows"]["kept"] == 1


def test_시험_묶음은_빼고_저장한_것과_견준다(home):
    put(home, "a" * 32, [row()], [dict(row(), _i=0)])
    put(home, "c" * 32, [row()], [dict(row(), _i=0)], case=None, source="upload")
    assert len(A.load_sets()) == 1 and len(A.load_sets(all_jobs=True)) == 2
    env = dict(os.environ, WUXI_HOME=str(home), PYTHONUTF8="1")
    run = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "accuracy.py")]
    p = subprocess.run(run + ["--save", "기준선"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 0, p.stderr
    assert "Main 칸 정확도" in p.stdout and len(os.listdir(home / "accuracy")) == 1
    p = subprocess.run(run + ["--compare", "기준선"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 0 and "(같음)" in p.stdout
