"""운영 화면 API 와 확인 화면의 학습 신호. AI 를 부르지 않는다(작업 결과를 직접 넣는다)."""
import pytest
from fastapi.testclient import TestClient

import wuxi_paths
import wuxi_rules as R


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(wuxi_paths, "WORK", str(tmp_path))
    R._cache.update(key=None, state=None)
    import wuxi_web
    wuxi_web.STORE.jobs.clear()
    wuxi_web.STORE.loaded = True
    local = TestClient(wuxi_web.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000))
    return wuxi_web, local


def put_job(web, supplier=None):
    items = [{"Item": "00010", "Purchase Order": "45", "Material": "", "Vendor Part No.": "", "B/L quantity": "1",
              "Invoice amount": "10", "Net Price": "10", "Imp.Req. No.": "", "품명": "PUMP SEAL", "단위": "PCS.", "원산지": "KOREA"}]
    job = {"id": "b" * 32, "status": "done", "created": __import__("time").time(), "names": ["00_a.pdf"], "pdfs": [], "steps": [], "supplier": supplier,
           "payload": {"items": items, "head": {}, "inv": [], "review": {}, "cols": {"gerp": ["Imp.Req. No.", "Item", "Purchase Order",
                       "Material", "Vendor Part No.", "B/L quantity", "Invoice amount", "Net Price"], "extra": ["품명", "단위", "원산지"]},
                       "completeness": {"verdict": "빠진 줄 의심", "checks": [{"check": "lines_pl", "name": "포장명세서 줄 수", "state": "bad", "note": "1줄, 2줄"}]},
                       "rules": [], "pages": [{"file": "a.pdf", "no": 1, "kind": "CI"}], "counts": {}, "required": [["Purchase Order"]]}}
    web.STORE.jobs[job["id"]] = job
    return job


def test_처음_설정은_이_PC_에서만(env):
    web, local = env
    remote = TestClient(web.app)
    assert remote.post("/api/admin/setup", json={"admin": "aaaaaaaa"}).status_code == 403
    assert local.get("/api/admin/state").json()["setup"] is True
    assert local.post("/api/admin/setup", json={"admin": "short"}).status_code == 400
    assert local.post("/api/admin/setup", json={"admin": "admin-pass-1", "user": "user-pass-1", "name": "가"}).json()["ok"]
    assert local.post("/api/admin/setup", json={"admin": "admin-pass-2"}).status_code == 409
    assert remote.get("/api/admin/rules").status_code == 401


def test_운영_비밀번호와_고객_비밀번호는_따로(env):
    web, local = env
    local.post("/api/admin/setup", json={"admin": "admin-pass-1", "user": "user-pass-1"})
    c = TestClient(web.app)
    assert c.post("/api/login", json={"password": "user-pass-1"}).json()["ok"]
    assert c.get("/api/jobs").status_code == 200 and c.get("/api/admin/overview").status_code == 401
    assert c.post("/api/admin/login", json={"password": "user-pass-1"}).status_code == 401
    assert c.post("/api/admin/login", json={"password": "admin-pass-1", "name": "나"}).json()["ok"]
    assert c.get("/api/admin/overview").status_code == 200


def test_엑셀을_받으면_고친_칸이_후보가_되고_반영된다(env):
    web, local = env
    local.post("/api/admin/setup", json={"admin": "admin-pass-1", "user": "user-pass-1"})
    sid = R.save_supplier("ACME PARTS LTD")["id"]
    job = put_job(web, sid)
    items = [dict(job["payload"]["items"][0], _i=0, Material="P3187097", **{"단위": "PCS"})]
    r = local.post(f"/api/jobs/{job['id']}/xlsx", json={"items": items, "deleted": []})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxml")
    lv = local.get("/api/admin/learn").json()
    kinds = sorted(c["kind"] for c in lv["candidates"])
    assert kinds == ["alias", "code"] and lv["feedback"]["cells"] == 2
    code = next(c for c in lv["candidates"] if c["kind"] == "code")
    assert code["preview"]["computable"] and code["preview"]["good"] == 1
    out = local.post(f"/api/admin/rules/{code['id']}/activate", json={"reason": "시험", "scope": "*"}).json()
    assert out["rule"]["status"] == "active" and out["rule"]["scope"] == "*" and out["rule"]["promoted_from"] == sid
    d = local.get(f"/api/admin/jobs/{job['id']}").json()
    assert d["edited"] == 2 and d["downloads"] == 1


def test_빠진_줄_판정과_확정(env):
    web, local = env
    local.post("/api/admin/setup", json={"admin": "admin-pass-1"})
    job = put_job(web, R.save_supplier("ACME PARTS LTD")["id"])
    assert local.post(f"/api/jobs/{job['id']}/review", json={"result": "maybe"}).status_code == 400
    out = local.post(f"/api/jobs/{job['id']}/review", json={"result": "ok"}).json()
    assert R.get(out["candidates"][0])["data"]["check"] == "lines_pl"
    assert local.post(f"/api/jobs/{job['id']}/confirm", json={"items": [dict(job["payload"]["items"][0], _i=0)]}).json()["ok"]
    row = local.get("/api/admin/overview").json()["jobs"][0]
    assert row["confirmed"] and row["review"] == "ok"


def test_설정과_비밀번호_바꾸기(env, monkeypatch):
    web, local = env
    import wuxi_admin
    monkeypatch.setattr(wuxi_admin, "claude_status", lambda force=False: {"found": True, "loggedIn": True, "bin": "claude",
                                                                         "configDirShown": "기본", "version": "x"})
    local.post("/api/admin/setup", json={"admin": "admin-pass-1"})
    assert local.post("/api/admin/settings", json={"model": "gpt-4"}).status_code == 400
    assert local.post("/api/admin/settings", json={"keepDays": 400}).status_code == 400
    assert local.post("/api/admin/settings", json={"model": "claude-sonnet-5-5", "keepDays": 30, "configDir": "/tmp/cfg"}).json()["ok"]
    s = local.get("/api/admin/settings").json()
    assert s["model"] == "claude-sonnet-5-5" and s["keepDays"] == 30 and s["configDir"] == "/tmp/cfg"
    assert wuxi_paths.claude_config_dir() == "/tmp/cfg" and wuxi_paths.model() == "claude-sonnet-5-5"
    assert local.post("/api/admin/password", json={"who": "user", "password": "new-user-pass"}).json()["ok"]
    c = TestClient(web.app)
    assert c.post("/api/login", json={"password": "new-user-pass"}).json()["ok"]
    assert any(h["event"] == "settings" for h in local.get("/api/admin/history").json()["rows"])


def test_규칙_화면_API(env):
    web, local = env
    local.post("/api/admin/setup", json={"admin": "admin-pass-1"})
    assert local.post("/api/admin/rules", json={"kind": "alias", "scope": "*", "data": {"field": "unit", "from": "", "to": "X"}}).status_code == 400
    r = local.post("/api/admin/rules", json={"kind": "alias", "scope": "*", "data": {"field": "unit", "from": "PCS.", "to": "PCS"}}).json()
    assert local.post("/api/admin/rules", json={"kind": "alias", "scope": "*", "data": {"field": "unit", "from": "pcs.", "to": "PCS"}}).status_code == 400
    assert local.post(f"/api/admin/rules/{r['rule']['id']}/off", json={}).json()["rule"]["status"] == "off"
    assert local.post("/api/admin/suppliers", json={"name": "ACME", "match": "ACME, ACME PARTS"}).json()["supplier"]["match"] == ["ACME", "ACME PARTS"]
    v = local.get("/api/admin/rules").json()
    assert v["suppliers"][0]["name"] == "ACME" and v["counts"]["off"] == 1
    data = local.get("/api/admin/export").json()
    assert local.post("/api/admin/import", json={"data": data}).json()["ok"]


def test_쿠키_서명_열쇠는_주인만_읽고_환경변수가_먼저(env, monkeypatch):
    import os
    import stat
    import threading
    web, _ = env
    monkeypatch.delenv("WUXI_SESSION_SECRET", raising=False)
    keys = []
    ts = [threading.Thread(target=lambda: keys.append(web._secret_key())) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(keys)) == 1 and len(keys[0]) == 64
    f = os.path.join(web.wuxi_paths.WORK, ".secret")
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
        os.chmod(f, 0o644)
        web._secret_key()
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    monkeypatch.setenv("WUXI_SESSION_SECRET", "from-env")
    assert web._secret_key() == "from-env"
    assert web.cookie_ok(web.make_cookie("user"), "user")
