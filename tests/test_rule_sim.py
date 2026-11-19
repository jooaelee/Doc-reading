"""규칙 시뮬레이션(tools/rule_sim.py) 잠금. 확정한 정답에 규칙을 미리 덧입혀 정확도 전과 후를 잰다. AI 를 부르지 않는다."""
import json

import pytest
from fastapi.testclient import TestClient

import accuracy as A
import rule_sim as S
import wuxi_paths
import wuxi_rules as R


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    R._cache.update(key=None, state=None)
    yield tmp_path


def row(**kw):
    base = {"Invoice No.": "INV1", "Purchase Order": "4302056146", "Material": "", "품명": "SEAL KIT",
            "Vendor Part No.": "4400364", "B/L quantity": 2, "단위": "EA", "Net Price": 10.5, "Currency": "USD", "출처": "CI"}
    base.update(kw)
    return base


def put(home, jid, items, gold, case, supplier="S1", checks=None):
    d = home / "jobs" / jid
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({
        "id": jid, "status": "done", "case": case, "source": "bulk", "created": 1, "started": 100.0,
        "supplier": supplier, "supplier_name": supplier,
        "payload": {"items": items, "completeness": {"checks": checks or []}}}), encoding="utf-8")
    (d / "golden.json").write_text(json.dumps({"time": 200.0, "items": gold}), encoding="utf-8")


def code(field, key, value, scope="S1", on="part", mode="fill"):
    return {"id": "r1", "kind": "code", "scope": scope, "status": "candidate",
            "data": {"on": on, "key": key, "field": field, "value": value, "mode": mode}}


def test_빈_칸을_정답대로_채우면_정확도가_오른다(home):
    put(home, "a" * 32, [row(), row(**{"Vendor Part No.": "1766531", "품명": "O-RING"})],
        [dict(row(Material="P2165141"), _i=0), dict(row(**{"Vendor Part No.": "1766531", "품명": "O-RING"}), _i=1)], "C1")
    put(home, "b" * 32, [row()], [dict(row(), _i=0)], "C2", supplier="S2")          # 다른 공급사: 범위 밖
    sets = A.load_sets()
    res = S.simulate(code("sap_code", "4400364", "P2165141"), sets)
    assert res["computable"] and res["touched"] == 1 and res["scopeCases"] == 1
    assert (res["fixed"], res["broken"], res["wrong"]) == (1, 0, 0)
    assert res["after"] > res["before"] and res["ok1"] - res["ok0"] == 1
    assert [s["name"] for s in res["suppliers"]] == ["S1"]
    assert "에서" in S.text(res) and "바로잡는 칸 1" in S.text(res)
    common = S.simulate(code("sap_code", "4400364", "P2165141"), sets, R.COMMON)      # 공통이면 S2 의 빈 칸도 채운다
    assert common["touched"] == 2 and common["broken"] == 1                          # S2 정답은 빈 칸이라 맞던 칸이 틀어짐


def test_맞던_칸을_바꾸면_틀어지는_칸으로_센다(home):
    put(home, "a" * 32, [row(Material="P1")], [dict(row(Material="P1"), _i=0)], "C1")
    res = S.simulate(code("sap_code", "4400364", "P9", mode="replace"), A.load_sets())
    assert (res["fixed"], res["broken"]) == (0, 1) and res["after"] < res["before"]
    keep = S.simulate(code("sap_code", "4400364", "P9"), A.load_sets())               # 채우기만 하는 규칙은 손대지 않음
    assert keep["touched"] == 0 and "그대로" in S.text(keep)


def test_포장명세서_줄에는_코드_사전을_쓰지_않는다(home):
    put(home, "a" * 32, [row(**{"출처": "PL"})], [dict(row(**{"출처": "PL"}), _i=0)], "C1")
    assert S.simulate(code("sap_code", "4400364", "P1"), A.load_sets())["touched"] == 0


def test_검사를_끄면_풀리는_건과_놓칠_건(home):
    bad = [{"check": "lines_pl", "state": "bad"}]
    put(home, "a" * 32, [row()], [dict(row(), _i=0)], "OK1", checks=bad)                       # 줄이 다 맞음
    put(home, "b" * 32, [row()], [dict(row(), _i=0), row(**{"품명": "BLADE", "Vendor Part No.": "B1"})], "MISS", checks=bad)
    put(home, "c" * 32, [row()], [dict(row(), _i=0)], "BOTH", checks=bad + [{"check": "amount", "state": "bad"}])
    rule = {"id": "r2", "kind": "check", "scope": "S1", "status": "candidate", "data": {"check": "lines_pl", "enabled": False}}
    res = S.simulate(rule, A.load_sets())
    assert (res["flagged"], res["cleared"], res["missed"]) == (3, 2, 1)             # BOTH 는 금액이 여전히 어긋남
    assert "놓칩니다" in S.text(res)


def test_열_이름과_판독_지침은_미리_잴_수_없다(home):
    put(home, "a" * 32, [row()], [dict(row(), _i=0)], "C1")
    rule = {"id": "r3", "kind": "header", "scope": "S1", "data": {"field": "sap_code", "header": "客户料号"}}
    res = S.simulate(rule, A.load_sets())
    assert not res["computable"] and res["scopeCases"] == 1 and "다시 읽기" in S.text(res)


def test_격상은_이미_쓰이는_공급사를_빼고_잰다(home):
    put(home, "a" * 32, [row()], [dict(row(Material="P2165141"), _i=0)], "C1")
    put(home, "b" * 32, [row()], [dict(row(Material="P2165141"), _i=0)], "C2", supplier="S2")
    res = S.simulate(code("sap_code", "4400364", "P2165141"), A.load_sets(), R.COMMON, "S1")
    assert res["touched"] == 1 and res["byCase"] == {"C2": {"fixed": 1, "broken": 0, "wrong": 0, "changed": 1}}


def test_후보_모두를_함께_잰다(home):
    put(home, "a" * 32, [row(), row(**{"Vendor Part No.": "1766531", "품명": "O-RING"})],
        [dict(row(Material="P2165141"), _i=0), dict(row(**{"Vendor Part No.": "1766531", "품명": "O-RING", "Material": "P1307809"}), _i=1)], "C1")
    rules = [code("sap_code", "4400364", "P2165141"), dict(code("sap_code", "1766531", "P1307809"), id="r9"),
             {"id": "h", "kind": "header", "scope": "S1", "data": {"field": "sap_code", "header": "X"}}]
    res = S.simulate_all(rules, A.load_sets())
    assert res["rules"] == 2 and res["skipped"] == 1 and res["fixed"] == 2 and res["after"] == 1.0


def test_운영_화면_학습에_정답_시뮬레이션이_붙는다(home):
    import wuxi_web
    wuxi_web.STORE.jobs.clear()
    wuxi_web.STORE.loaded = False
    local = TestClient(wuxi_web.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000))
    local.post("/api/admin/setup", json={"admin": "admin-pass-1"})
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    put(home, "a" * 32, [row()], [dict(row(Material="P2165141"), _i=0)], "C1", supplier=sid)
    R.upsert_candidate("code", sid, {"on": "part", "key": "4400364", "field": "sap_code", "value": "P2165141"},
                       {"job": "a" * 32, "time": 1, "row": 0, "col": "Material", "before": "", "after": "P2165141", "supplier": sid})
    lv = local.get("/api/admin/learn").json()
    c = lv["candidates"][0]
    assert lv["goldCases"] == 1 and c["sim"]["fixed"] == 1 and c["sim"]["after"] == 1.0
    assert c["simCommon"]["computable"] and lv["simAll"]["fixed"] == 1
