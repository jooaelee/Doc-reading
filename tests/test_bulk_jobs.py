"""건별 폴더를 고객 화면 작업 목록에 대기 작업으로 넣는 도구(tools/bulk_jobs.py). AI 를 부르지 않는다."""
import json

import pytest

import wuxi_paths

PDF = b"%PDF-1.4\n%fake\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setattr(wuxi_paths, "WORK", str(work))
    import bulk_jobs
    import wuxi_web
    monkeypatch.setattr(bulk_jobs, "server_up", lambda port=8767: False)
    root = tmp_path / "hx"
    for case, files in {"CASE_A": ["HB_1_A.pdf", "OH_2_A.PDF"], "CASE_B": ["HB_1_B.pdf"], "EMPTY": []}.items():
        (root / case).mkdir(parents=True)
        for f in files:
            (root / case / f).write_bytes(PDF)
    return bulk_jobs, wuxi_web, root


def jobs(web):
    out = []
    for d in web.jobs_root().iterdir():
        out.append(json.loads((d / "job.json").read_text(encoding="utf-8")))
    return sorted(out, key=lambda j: j["created"])


def test_건마다_대기_작업_하나를_화면_업로드와_같은_모양으로_넣는다(env):
    bulk, web, root = env
    assert bulk.main([str(root)]) == 0
    js = jobs(web)
    assert [j["case"] for j in js] == ["CASE_A", "CASE_B"]          # PDF 없는 폴더는 뺀다
    a = js[0]
    assert a["status"] == "queued" and a["title"] == "CASE_A" and a["source"] == "bulk"
    assert a["names"] == ["00_HB_1_A.pdf", "01_OH_2_A.PDF"]          # 대문자 확장자도 받는다
    assert "pdfs" not in a                                             # 화면 업로드처럼 경로는 저장하지 않는다
    assert (web.jobs_root() / a["id"] / "00_HB_1_A.pdf").read_bytes() == PDF
    assert web.JOB_ID.match(a["id"])


def test_같은_건은_두_번_넣지_않는다(env):
    bulk, web, root = env
    bulk.main([str(root)])
    bulk.main([str(root)])
    assert len(jobs(web)) == 2


def test_서버가_켜질_때_넣은_차례대로_읽는다(env, monkeypatch):
    bulk, web, root = env
    batch = root.parent / "batch.json"
    batch.write_text(json.dumps([{"case": "CASE_A", "completeness": {"verdict": "빠진 줄 의심"}},
                                 {"case": "CASE_B", "completeness": {"verdict": "이상 없음"}}]), encoding="utf-8")
    bulk.main([str(root), "--order", str(batch)])
    store = web.Store()
    monkeypatch.setattr(store, "start", lambda: None)                 # 일꾼은 띄우지 않는다
    store.load_all()
    order = [store.jobs[j]["case"] for j in list(store.q.queue)]
    assert order == ["CASE_B", "CASE_A"]                              # 「이상 없음」 건이 먼저
    assert all(len(store.jobs[j]["pdfs"]) == len(store.jobs[j]["names"]) for j in store.q.queue)


def test_PDF_가_아니면_그_건만_건너뛴다(env):
    bulk, web, root = env
    (root / "CASE_B" / "HB_1_B.pdf").write_bytes(b"not a pdf")
    bulk.main([str(root)])
    assert [j["case"] for j in jobs(web)] == ["CASE_A"]
    assert len(list(web.jobs_root().iterdir())) == 1                 # 반쯤 만든 폴더를 남기지 않는다


def saved(web, jid):
    return json.loads((web.jobs_root() / jid / "job.json").read_text(encoding="utf-8"))["payload"]


def test_통화_열_전에_읽은_작업에_통화를_채운다(env):
    bulk, web, root = env
    import jobs_backfill
    job = {"id": "c" * 32, "status": "done", "created": 1, "names": [], "case": "CASE_A",
           "payload": {"items": [{"출처": "CI", "Invoice No.": "I1", "Net Price": 1}, {"출처": "CI", "Invoice No.": "I1"}],
                       "inv": [{"Invoice No.": "I1", "통화": "JPY"}],
                       "cols": {"gerp": ["Net Price"], "extra": ["케이스", "Invoice No."]}}}
    web.STORE.save(job)
    assert jobs_backfill.main([]) == 0
    got = saved(web, job["id"])
    assert [r["Currency"] for r in got["items"]] == ["JPY", "JPY"]
    assert got["cols"]["extra"][0] == "Currency"
    assert jobs_backfill.main([]) == 0                                 # 두 번 돌려도 그대로
    assert saved(web, job["id"]) == got


def test_못_이은_포장_줄을_같은_수량의_인보이스_줄에_합친다(env):
    """AATSN2601238: 인보이스 1줄(코드 1014869, 264개)과 포장명세서 1줄(코드 없음, 264개)이 두 줄로 나왔다."""
    bulk, web, root = env
    import jobs_backfill
    items = [{"출처": "CI", "Invoice No.": "KA-1", "B/L quantity": 264, "Sap Code": "1014869"},
             {"출처": "PL", "Invoice No.": "", "B/L quantity": 264, "순중량(kg)": 9.24, "총중량(kg)": 12, "Case No.": ""}]
    job = {"id": "d" * 32, "status": "done", "created": 1, "names": [], "case": "AATSN",
           "payload": {"items": items, "inv": [], "cols": {"gerp": [], "extra": list(__import__("gerp_xlsx").EXTRA_ITEM_COLS)}}}
    web.STORE.save(job)
    done = dict(job, id="e" * 32, confirmed=1)                         # 확정한 작업은 줄 번호가 기록에 묶여 있다
    web.STORE.save(done)
    assert jobs_backfill.main([]) == 0
    got = saved(web, job["id"])["items"]
    assert len(got) == 1 and (got[0]["순중량(kg)"], got[0]["총중량(kg)"]) == (9.24, 12)
    assert len(saved(web, done["id"])["items"]) == 2


def test_서버가_켜져_있으면_고치지_않는다(env, monkeypatch):
    bulk, web, root = env
    import jobs_backfill
    monkeypatch.setattr(bulk, "server_up", lambda port=8767: True)
    assert jobs_backfill.main([]) == 2


def test_한곳에_모인_PDF_는_파일_하나가_건_하나다(env, capsys):
    """10월 자료: PDF 514개가 한 폴더에. 「SFSA2610153  正式件.pdf」 는 첫 낱말이 건 이름, 번호 없는 이름은 뺀다."""
    bulk, web, root = env
    for f in ("HDLCA126459.pdf", "SFSA2610153  正式件.pdf", "日本车板图.pdf", "HDLCA127308 TOK photo.zip"):
        (root / f).write_bytes(PDF)
    assert bulk.main([str(root), "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "남은 건 2건" in out and "日本车板图.pdf" in out
    assert [j["case"] for j in jobs(web)] == ["CASE_A", "CASE_B"]
    bulk.main([str(root)])
    assert [j["case"] for j in jobs(web)][2:] == ["HDLCA126459", "SFSA2610153"]


def test_한도에_걸리면_작업을_대기로_두고_쉬었다가_다시_읽는다(env, monkeypatch):
    bulk, web, root = env
    calls = []

    def fake_run(job, store):
        calls.append(job["id"])
        if len(calls) == 1:
            job.update(status="queued")
            return web.LIMITED
        job.update(status="done")
        return None

    import wuxi_engine

    class RateLimited(RuntimeError):
        pass

    def boom(*a, **k):
        raise RateLimited("claude CLI 한도: You've hit your usage limit")

    monkeypatch.setattr(wuxi_engine, "read_bundle", boom)
    store = web.Store()
    job = {"id": "f" * 32, "status": "queued", "created": 1, "names": [], "pdfs": [], "steps": [], "title": "X"}
    store.jobs[job["id"]] = job
    assert web.run(job, store) == web.LIMITED                        # 진짜 run: 오류가 아니라 대기로 되돌림
    assert job["status"] == "queued" and "한도" in job["steps"][0]["text"] and job["error"] is None
    slept = []
    monkeypatch.setattr(web, "run", fake_run)
    monkeypatch.setattr(web.time, "sleep", lambda s: slept.append(s))
    other = {"id": "a" * 32, "status": "queued", "created": 2}
    store.jobs[other["id"]] = other
    store.q.put(job["id"])
    store.q.put(other["id"])
    t = __import__("threading").Thread(target=store._loop, daemon=True)
    t.start()
    store.q.join()
    assert calls == [job["id"], job["id"], other["id"]]              # 같은 작업을 다시 읽고 나서 다음 작업
    assert slept == [web.LIMIT_WAIT] and job["status"] == "done"


def test_확정한_작업은_보관_기간이_지나도_지우지_않는다(env):
    bulk, web, root = env
    store = web.Store()
    old = {"id": "b" * 32, "status": "done", "created": 1, "names": []}
    gold = {"id": "c" * 32, "status": "done", "created": 1, "names": [], "confirmed": 5}
    for j in (old, gold):
        store.save(j)
        store.jobs[j["id"]] = j
    store.cleanup()
    assert set(store.jobs) == {gold["id"]} and (web.jobs_root() / gold["id"]).is_dir()


def test_설정_파일을_못_읽으면_정리를_건너뛴다(env):
    bulk, web, root = env
    store = web.Store()
    old = {"id": "b" * 32, "status": "done", "created": 1, "names": []}
    store.save(old)
    store.jobs[old["id"]] = old
    (web.jobs_root().parent / "settings.json").write_text("{깨진", encoding="utf-8")
    store.cleanup()
    assert old["id"] in store.jobs


def test_미리_보기는_아무것도_넣지_않는다(env, capsys):
    bulk, web, root = env
    bulk.main([str(root), "--dry-run"])
    assert not web.jobs_root().exists() or not list(web.jobs_root().iterdir())
    assert "넣을 건 2건" in capsys.readouterr().out


def test_작업_폴더에_닿기_전에_켜도_나중에_대기_작업을_읽는다(env, monkeypatch):
    """11/14: VPN 을 켜기 전에 서버를 켜 빈 목록을 읽은 것으로 쳐서, 넣어 둔 50건이 읽히지 않았다."""
    bulk, web, root = env
    store = web.Store()
    monkeypatch.setattr(store, "start", lambda: None)
    store.load_all()                                                  # 아직 작업 폴더가 없다(공유 폴더가 안 붙은 것과 같다)
    assert not store.loaded and not store.jobs
    bulk.main([str(root)])
    store.load_all()
    assert store.loaded and sorted(store.jobs[j]["case"] for j in store.q.queue) == ["CASE_A", "CASE_B"]


def test_켜져_있는_동안_새로_넣은_대기_작업만_줄에_세운다(env, monkeypatch):
    bulk, web, root = env
    bulk.main([str(root), "--cases", "CASE_A"])
    store = web.Store()
    monkeypatch.setattr(store, "start", lambda: None)
    store.load_all()
    assert len(store.q.queue) == 1
    bulk.main([str(root)])                                            # 서버가 켜진 뒤에 CASE_B 를 넣음
    done = {"id": "9" * 32, "status": "done", "created": 3, "names": []}
    store.save(done)                                                  # 다른 상태의 새 작업은 건드리지 않음
    assert store.rescan() == 1
    assert [store.jobs[j]["case"] for j in store.q.queue] == ["CASE_A", "CASE_B"]
    assert done["id"] not in store.jobs
    assert store.rescan() == 0                                        # 두 번 세우지 않음


def test_공용_폴더에서_잠깐_막혀도_결과를_저장한다(env, monkeypatch):
    """다른 프로그램이 job.json 을 잠깐 열고 있으면 바꿔 쓰기가 「액세스 거부」로 실패한다. 다 읽은 결과를 잃지 않는다."""
    _, web, _ = env
    import pathlib
    real = pathlib.Path.replace
    tries = []

    def flaky(self, target):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError(5, "액세스가 거부되었습니다")
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", flaky)
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    web.STORE.save({"id": "a" * 32, "status": "done", "payload": {"items": []}})
    assert len(tries) == 3
    assert json.loads((web.STORE.dir("a" * 32) / "job.json").read_text(encoding="utf-8"))["status"] == "done"


def test_계속_막히면_저장_실패를_알린다(env, monkeypatch):
    _, web, _ = env
    import pathlib

    def deny(self, target):
        raise PermissionError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(pathlib.Path, "replace", deny)
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        web.STORE.save({"id": "b" * 32, "status": "done"})
