"""하이닉스 우시 고객 화면(웹 앱). 공급사 서류를 올리면 G-ERP 품목 표로 읽고, 고쳐서 엑셀로 받는다.

docagent(KEROL 오더, B/L)와 별개다. 코드, 자료, 열쇠를 나누지 않는다. 혼자 띄울 수도 있고(아래 실행), docagent 서버가
/wuxi 에 붙여 같은 포트로 낼 수도 있다(docai.web 이 이 모듈의 app 을 붙임).

  python tools/wuxi_web.py start              # 처음 쓰는 사람용. 비밀번호를 묻고, Claude 로그인을 확인하고, 띄워서 브라우저를 연다
  python tools/wuxi_web.py set-password        # 고객 화면 비밀번호(해시만 남음)
  python tools/wuxi_web.py set-admin-password  # 운영 화면 비밀번호
  python tools/wuxi_web.py serve --port 8767   # 혼자 띄우기. http://127.0.0.1:8767/ (운영 화면은 /admin)

경로(앱 기준, docagent 에 붙이면 앞에 /wuxi)
  GET  /                      화면
  GET  /static/<파일>          pdf.js(저장소에 사본. 인터넷 없이 원본을 그린다)
  GET  /api/state             비밀번호를 정했는지, 들어왔는지, 한도
  POST /api/login, /api/logout
  GET  /api/jobs              최근 작업(보관 기간 안)
  POST /api/jobs              PDF 를 올려 판독을 줄 세운다
  GET  /api/jobs/{id}         진행 단계와 결과
  POST /api/jobs/{id}/retry   같은 서류로 다시 읽기
  DELETE /api/jobs/{id}       작업과 올린 서류 지우기
  GET  /api/jobs/{id}/pdf/{n} 올린 원본
  POST /api/jobs/{id}/xlsx    화면에서 고친 값으로 G-ERP 엑셀. 고친 칸은 학습 신호로 남는다
  POST /api/jobs/{id}/confirm 최종 값 확정(정답 세트)
  POST /api/jobs/{id}/review  「빠진 줄 의심」을 원본과 대조한 결과
  GET  /admin                 운영 화면(현황, 규칙, 학습, 이력, 설정). /api/admin/* 는 운영 비밀번호로 들어온 사람만

지키는 것
- 고객 화면 비밀번호와 운영 화면 비밀번호를 따로 둔다. 해시(PBKDF2)만 저장하고, 들어오면 12시간짜리 서명 쿠키를 준다.
  틀리면 IP 마다 1분에 5번까지. 해시는 환경변수, WUXI_HOME, 저장소 순으로 찾는다(화면에서 바꾸면 WUXI_HOME 에 남는다)
- 판독은 한 번에 하나(엔진 잠금). 일꾼 스레드 하나가 줄을 차례로 처리하고, 줄은 MAX_QUEUE 까지
- 작업은 디스크(WUXI_HOME/jobs/<id>)에 남아 서버가 다시 떠도 결과가 보인다. 도중에 끊긴 작업은 「다시 읽기」로 잇는다
- 올린 서류와 결과는 WUXI_KEEP_DAYS 일(기본 14) 뒤 지운다
- 누가 언제 무엇을 했는지(서류 내용 없이) WUXI_HOME/access.log 에 남긴다
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402
import wuxi_learn  # noqa: E402
import wuxi_rules  # noqa: E402

from fastapi import FastAPI, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402

BASE = Path(wuxi_paths.BASE)
PAGE = BASE / "web" / "index.html"
ADMIN_PAGE = BASE / "web" / "admin.html"
STATIC = BASE / "web" / "static"
ACCESS_FILE = BASE / "access.json"
PW_FILES = {"user": "access.json", "admin": "admin.json"}
PW_ENV = {"user": "WUXI_PASSWORD_HASH", "admin": "WUXI_ADMIN_HASH"}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_FILES = 30
MAX_MB = 40
MAX_QUEUE = 8
SESSION_HOURS = 12
COOKIE = "wuxi_session"
ADMIN_COOKIE = "wuxi_admin"
NO_STORE = {"Cache-Control": "no-store"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PBKDF2_ITER = 240_000


def jobs_root() -> Path:
    return Path(wuxi_paths.WORK) / "jobs"


def keep_days() -> int:
    try:
        return max(1, int(wuxi_paths.settings().get("keep_days") or os.environ.get("WUXI_KEEP_DAYS") or 14))
    except ValueError:
        return 14


# ───────────────────────────────────────── 비밀번호와 쿠키
def hash_password(pw: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITER)
    return {"algo": "pbkdf2_sha256", "iter": PBKDF2_ITER, "salt": base64.b64encode(salt).decode(),
            "hash": base64.b64encode(dk).decode()}


def stored_hash(who: str = "user") -> dict | None:
    """비밀번호 해시. 환경변수(WUXI_PASSWORD_HASH, WUXI_ADMIN_HASH), WUXI_HOME 의 파일, 저장소의 파일 순."""
    raw = os.environ.get(PW_ENV[who])
    try:
        if raw:
            return json.loads(raw)
        for f in (Path(wuxi_paths.WORK) / PW_FILES[who], BASE / PW_FILES[who]):
            if f.is_file():
                return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return None


def save_password(who: str, pw: str) -> Path:
    if len(pw or "") < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    f = Path(wuxi_paths.WORK) / PW_FILES[who]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(hash_password(pw), indent=1) + "\n", encoding="utf-8")
    return f


def check_password(pw: str, h: dict | None) -> bool:
    if not h or h.get("algo") != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(h["salt"]), int(h["iter"]))
    return hmac.compare_digest(base64.b64encode(dk).decode(), h["hash"])


def _secret_key() -> str:
    """쿠키 서명 열쇠. WUXI_SESSION_SECRET 이 있으면 그 값을 쓰고, 없으면 작업 폴더에 한 번 만든 파일을 쓴다.
    파일은 주인만 읽고 쓰게(0600) 만들고, 요청 두 개가 동시에 와도 한 번만 만든다."""
    env = (os.environ.get("WUXI_SESSION_SECRET") or "").strip()
    if env:
        return env
    f = Path(wuxi_paths.WORK) / ".secret"
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(secrets.token_hex(32))
    try:
        os.chmod(f, 0o600)                 # 예전 판이 누구나 읽게 만든 파일도 좁힌다(Windows 는 읽기 전용 표시만 바뀜)
    except OSError:
        pass
    for _ in range(50):                    # 다른 요청이 막 만들어 아직 비어 있으면 잠깐 기다린다
        key = f.read_text(encoding="utf-8").strip()
        if key:
            return key
        time.sleep(0.02)
    raise RuntimeError("쿠키 서명 열쇠 파일이 비어 있습니다: " + str(f))


def _server_secret(who: str = "user") -> bytes:
    """비밀번호를 바꾸면 해시가 섞여 예전 쿠키가 모두 풀린다."""
    h = stored_hash(who) or {}
    return hashlib.sha256((_secret_key() + who + h.get("hash", "")).encode()).digest()


def make_cookie(who: str = "user", name: str = "") -> str:
    exp = int(time.time()) + SESSION_HOURS * 3600
    nm = base64.urlsafe_b64encode(name.encode("utf-8")[:60]).decode().rstrip("=")
    sig = hmac.new(_server_secret(who), f"{exp}.{nm}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{nm}.{sig}"


def cookie_ok(value: str | None, who: str = "user") -> bool:
    return cookie_name(value, who) is not None


def cookie_name(value: str | None, who: str = "user") -> str | None:
    """쿠키가 맞으면 들어온 사람의 이름(없으면 빈 글), 틀리면 None."""
    try:
        exp, nm, sig = (value or "").split(".", 2)
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    want = hmac.new(_server_secret(who), f"{exp}.{nm}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return None
    try:
        return base64.urlsafe_b64decode(nm + "=" * (-len(nm) % 4)).decode("utf-8")
    except ValueError:
        return ""


_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _too_many(ip: str) -> bool:
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < 60]
        _fails[ip] = recent
        return len(recent) >= 5


def _failed(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


# ───────────────────────────────────────── 기록
def log_access(request: Request | None, action: str, job: str = "", status: int = 200, note: str = "") -> None:
    try:
        f = Path(wuxi_paths.WORK) / "access.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        ip = request.client.host if request is not None and request.client else ""
        with f.open("a", encoding="utf-8") as fo:
            fo.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "ip": ip, "action": action,
                                 "job": job[:8], "status": status, "note": note[:120]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ───────────────────────────────────────── 작업과 일꾼
class Store:
    """작업은 메모리와 디스크(job.json) 양쪽에. 화면이 보는 값은 늘 디스크에도 있다."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.q: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.loaded = False

    def dir(self, jid: str) -> Path:
        return jobs_root() / jid

    def save(self, job: dict) -> None:
        d = self.dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in job.items() if k not in ("pdfs",)}
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "job.json")

    def load_all(self) -> None:
        """서버가 뜰 때 한 번. 줄에 있던 작업은 다시 줄 세우고, 읽던 작업은 끊김으로 표시한다."""
        with self.lock:
            if self.loaded:
                return
            self.loaded = True
            root = jobs_root()
            if not root.is_dir():
                return
            waiting = []
            for d in root.iterdir():
                f = d / "job.json"
                if not (JOB_ID.match(d.name) and f.is_file()):
                    continue
                try:
                    job = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                job["pdfs"] = [str(d / n) for n in job.get("names") or []]
                if job.get("status") == "running":
                    job.update(status="error", error="읽는 도중 서버가 다시 시작되었습니다. 「다시 읽기」를 눌러 주십시오.")
                    self.save(job)
                elif job.get("status") == "queued":
                    waiting.append(job)
                self.jobs[job["id"]] = job
            for job in sorted(waiting, key=lambda j: j.get("created") or 0):
                self.q.put(job["id"])
            if waiting:
                self.start()

    def get(self, jid: str) -> dict | None:
        self.load_all()
        return self.jobs.get(jid) if JOB_ID.match(jid or "") else None

    def queue_pos(self, jid: str) -> int:
        with self.q.mutex:
            items = list(self.q.queue)
        return items.index(jid) + 1 if jid in items else 0

    def waiting(self) -> int:
        return sum(1 for j in self.jobs.values() if j.get("status") in ("queued", "running"))

    def submit(self, job: dict) -> None:
        with self.lock:
            self.jobs[job["id"]] = job
            self.save(job)
            self.q.put(job["id"])
            self.start()

    def start(self) -> None:
        with self.lock:
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._loop, daemon=True, name="wuxi-worker")
                self.worker.start()

    def _loop(self) -> None:
        while True:
            jid = self.q.get()
            job = self.jobs.get(jid)
            if job and job.get("status") == "queued":
                try:
                    run(job, self)
                except Exception as e:      # 일꾼은 죽지 않는다
                    job.update(status="error", error=str(e)[:300])
                    self.save(job)
            self.q.task_done()

    def cleanup(self) -> None:
        cut = time.time() - keep_days() * 86400
        with self.lock:
            for jid, job in list(self.jobs.items()):
                if (job.get("created") or 0) < cut and job.get("status") not in ("queued", "running"):
                    shutil.rmtree(self.dir(jid), ignore_errors=True)
                    self.jobs.pop(jid, None)


STORE = Store()


def run(job: dict, store: Store) -> None:
    """작업 하나를 읽는다. 단계는 화면이 1.5초마다 받아 간다."""
    import wuxi_engine

    def on_step(text, done, sec):
        if done:
            for st in reversed(job["steps"]):
                if not st["done"] and st["text"] == text:
                    st.update(done=True, sec=round(sec or 0, 1))
                    store.save(job)
                    return
        job["steps"].append({"text": text, "done": bool(done), "sec": round(sec, 1) if sec else None})
        store.save(job)

    job.update(status="running", started=time.time(), steps=[], error=None, traceback=None, payload=None)
    store.save(job)
    try:
        res = wuxi_engine.read_bundle(job["pdfs"], name=job.get("title") or "올린 서류", on_step=on_step)
        job["payload"] = wuxi_engine.payload(job.get("title") or "올린 서류", job["pdfs"], res)
        job.update(status="done", wall=round(time.time() - job["started"], 1), supplier=res.get("supplier"),
                   supplier_name=res.get("supplier_name"), edits=None, review=None, confirmed=None)
        try:
            wuxi_rules.record_applied(res.get("trace") or [], job["id"])
        except Exception:      # 적용 건수를 못 올려도 결과는 낸다
            pass
    except Exception as e:
        # 화면에는 짧은 문장만 보인다. 어디서 났는지는 job.json 의 traceback 과 서버 창에 남긴다
        import traceback
        tb = traceback.format_exc()
        sys.stderr.write(f"작업 {job.get('id')} 판독 오류\n{tb}")
        job.update(status="error", error=wuxi_engine.friendly_error(str(e)), traceback=tb[-4000:],
                   wall=round(time.time() - job["started"], 1))
    store.save(job)


def _summary(job: dict) -> dict:
    p = job.get("payload") or {}
    comp = p.get("completeness") or {}
    return {"id": job["id"], "status": job.get("status"), "created": job.get("created"), "files": _files(job),
            "lines": len(p.get("items") or []), "verdict": comp.get("verdict"),
            "fail": (p.get("counts") or {}).get("FAIL"), "wall": job.get("wall"),
            "supplier": job.get("supplier_name") or "", "confirmed": bool(job.get("confirmed"))}


def _files(job: dict) -> list[str]:
    return [re.sub(r"^\d\d_", "", n) for n in job.get("names") or []]


_FORMULA = re.compile(r"^[=+@\t\r]")


def plain(v):
    """엑셀이 수식으로 읽지 않게 한다(=, +, @ 로 시작하는 글자). 서류 글자와 화면에서 고친 값이 그대로 들어간다."""
    if isinstance(v, str) and _FORMULA.match(v):
        return "'" + v
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


# ───────────────────────────────────────── 앱
app = FastAPI(title="Hynix Wuxi G-ERP item reader", docs_url=None, redoc_url=None, openapi_url=None)


def _authed(request: Request) -> bool:
    """고객 화면. 운영 비밀번호로 들어온 사람도 볼 수 있다."""
    return cookie_ok(request.cookies.get(COOKIE)) or _admin(request) is not None


def _admin(request: Request) -> str | None:
    """운영 화면에 들어온 사람의 이름. 안 들어왔으면 None."""
    return cookie_name(request.cookies.get(ADMIN_COOKIE), "admin")


def _who(request: Request) -> str:
    name = _admin(request)
    ip = request.client.host if request.client else ""
    return (name or "관리자") + (f" ({ip})" if ip else "")


def _need_login() -> JSONResponse:
    return JSONResponse({"error": "비밀번호를 넣어 주십시오.", "login": True}, 401)


def _bad(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, code)


@app.get("/", include_in_schema=False)
def index() -> Response:
    return FileResponse(PAGE, media_type="text/html; charset=utf-8", headers=NO_STORE)


@app.get("/static/{name}", include_in_schema=False)
def static(name: str) -> Response:
    f = STATIC / name
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or not f.is_file():
        return _bad("없는 파일입니다", 404)
    media = "text/javascript" if name.endswith((".mjs", ".js")) else None
    return FileResponse(f, media_type=media, headers={"Cache-Control": "max-age=86400"})


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def admin_index() -> Response:
    return FileResponse(ADMIN_PAGE, media_type="text/html; charset=utf-8", headers=NO_STORE)


@app.get("/api/state")
def state(request: Request) -> JSONResponse:
    return JSONResponse({"configured": stored_hash() is not None, "auth": _authed(request),
                         "admin": _admin(request) is not None,
                         "limits": {"files": MAX_FILES, "mb": MAX_MB, "keepDays": keep_days()}}, headers=NO_STORE)


@app.post("/api/login")
async def login(request: Request) -> Response:
    ip = request.client.host if request.client else ""
    if stored_hash() is None:
        return _bad("화면 비밀번호가 아직 정해지지 않았습니다. 담당자에게 문의해 주십시오.", 503)
    if _too_many(ip):
        log_access(request, "login", status=429)
        return _bad("비밀번호를 여러 번 틀렸습니다. 1분 뒤 다시 해 주십시오.", 429)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    pw = str((body or {}).get("password") or "")[:200]
    if not check_password(pw, stored_hash()):
        _failed(ip)
        log_access(request, "login", status=401)
        return _bad("비밀번호가 맞지 않습니다.", 401)
    log_access(request, "login")
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, make_cookie(), max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax",
                 path=(request.scope.get("root_path") or "") + "/")
    return r


@app.post("/api/logout")
def logout(request: Request) -> Response:
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path=(request.scope.get("root_path") or "") + "/")
    return r


@app.get("/api/jobs")
def list_jobs(request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    STORE.load_all()
    rows = sorted((_summary(j) for j in STORE.jobs.values()), key=lambda r: r["created"] or 0, reverse=True)
    return JSONResponse({"jobs": rows[:100]}, headers=NO_STORE)


@app.post("/api/jobs")
async def create_job(request: Request, pdf: list[UploadFile]) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    STORE.load_all()
    files = [f for f in pdf if f.filename]
    if not files:
        return _bad("PDF 파일을 골라 주십시오.")
    if len(files) > MAX_FILES:
        return _bad(f"한 번에 {MAX_FILES}개까지 올릴 수 있습니다.")
    if STORE.waiting() >= MAX_QUEUE:
        return _bad("앞에 기다리는 작업이 많습니다. 잠시 뒤 올려 주십시오.", 429)
    STORE.cleanup()
    jid = uuid.uuid4().hex
    d = STORE.dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    names = []
    for i, f in enumerate(files):
        data = await f.read(MAX_MB * 1024 * 1024 + 1)
        base = Path(f.filename or "").name
        if len(data) > MAX_MB * 1024 * 1024:
            shutil.rmtree(d, ignore_errors=True)
            return _bad(f"파일 하나는 {MAX_MB}MB 까지입니다: {base[:60]}")
        if not data.lstrip()[:5].startswith(b"%PDF"):
            shutil.rmtree(d, ignore_errors=True)
            return _bad(f"PDF 가 아닌 파일이 있습니다: {base[:60]}")
        safe = re.sub(r"[^A-Za-z0-9._\-가-힣()\[\] ]", "_", base)[-90:] or "doc.pdf"
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        name = f"{i:02d}_{safe}"
        (d / name).write_bytes(data)
        names.append(name)
    job = {"id": jid, "status": "queued", "created": time.time(), "names": names, "pdfs": [str(d / n) for n in names],
           "steps": [], "payload": None, "error": None}
    STORE.submit(job)
    log_access(request, "upload", jid, note=f"{len(names)} files")
    return JSONResponse({"job": jid})


@app.get("/api/jobs/{jid}")
def get_job(jid: str, request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job:
        return _bad("작업을 찾을 수 없습니다. 보관 기간이 지났을 수 있습니다.", 404)
    out = {k: job.get(k) for k in ("id", "status", "steps", "wall", "error", "payload", "created")}
    out["files"] = _files(job)
    out["queue"] = STORE.queue_pos(jid)
    return JSONResponse(out, headers=NO_STORE)


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str, request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job:
        return _bad("작업을 찾을 수 없습니다.", 404)
    if job.get("status") in ("queued", "running"):
        return _bad("이미 읽는 중입니다.")
    if not all(Path(p).is_file() for p in job["pdfs"]):
        return _bad("올린 서류가 남아 있지 않습니다. 다시 올려 주십시오.", 410)
    job.update(status="queued", steps=[], error=None, payload=None)
    STORE.submit(job)
    log_access(request, "retry", jid)
    return JSONResponse({"job": jid})


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job:
        return _bad("작업을 찾을 수 없습니다.", 404)
    if job.get("status") == "running":
        return _bad("읽는 중에는 지울 수 없습니다.")
    with STORE.lock:
        job["status"] = "deleted"
        STORE.jobs.pop(jid, None)
        shutil.rmtree(STORE.dir(jid), ignore_errors=True)
    log_access(request, "delete", jid)
    return JSONResponse({"ok": True})


@app.get("/api/jobs/{jid}/pdf/{n}")
def original(jid: str, n: int, request: Request) -> Response:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job or not (0 <= n < len(job.get("pdfs") or [])) or not Path(job["pdfs"][n]).is_file():
        return _bad("원본을 찾을 수 없습니다", 404)
    return FileResponse(job["pdfs"][n], media_type="application/pdf", headers=NO_STORE)


@app.post("/api/jobs/{jid}/xlsx")
async def xlsx(jid: str, request: Request) -> Response:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job or not job.get("payload"):
        return _bad("결과가 없습니다", 404)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    p = job["payload"]
    items = body.get("items") if isinstance(body.get("items"), list) else p["items"]
    items = [x for x in items[:5000] if isinstance(x, dict)]
    if isinstance(body.get("items"), list):
        try:
            with STORE.lock:
                wuxi_learn.record_edits(job, items, body.get("deleted"), by=_who(request))
                wuxi_learn.save_final(job, items)
                job["downloads"] = (job.get("downloads") or 0) + 1
                STORE.save(job)
        except Exception as e:     # 학습 기록이 실패해도 엑셀은 낸다
            log_access(request, "learn-error", jid, 500, str(e))
    t = {"head": [body.get("head") if isinstance(body.get("head"), dict) else p["head"]],
         "items": [x for x in items[:5000] if isinstance(x, dict)],
         "inv": body.get("inv") if isinstance(body.get("inv"), list) else p["inv"],
         "review": [p["review"]]}
    import gerp_xlsx
    out = STORE.dir(jid) / "gerp.xlsx"
    gerp_xlsx.write_tables(plain(t), str(out))
    inv = next((str(x.get("Invoice No.") or "") for x in t["items"] if x.get("Invoice No.")), "")
    fname = "GERP_" + (re.sub(r"[^A-Za-z0-9._-]", "_", inv)[:40] or jid[:8]) + ".xlsx"
    log_access(request, "xlsx", jid, note=f"{len(t['items'])} rows")
    return Response(out.read_bytes(), media_type=XLSX,
                    headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/jobs/{jid}/confirm")
async def confirm(jid: str, request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job or not job.get("payload"):
        return _bad("결과가 없습니다", 404)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    items = [x for x in ((body or {}).get("items") or [])[:5000] if isinstance(x, dict)]
    if not items:
        return _bad("확정할 줄이 없습니다.")
    with STORE.lock:
        summary = wuxi_learn.confirm(job, items, by=_who(request))
        STORE.save(job)
    log_access(request, "confirm", jid, note=f"{len(items)} rows")
    return JSONResponse({"ok": True, "edits": summary})


@app.post("/api/jobs/{jid}/review")
async def review(jid: str, request: Request) -> JSONResponse:
    if not _authed(request):
        return _need_login()
    job = STORE.get(jid)
    if not job or not job.get("payload"):
        return _bad("결과가 없습니다", 404)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    result = str((body or {}).get("result") or "")
    if result not in ("ok", "missing"):
        return _bad("판정은 ok 또는 missing 입니다.")
    with STORE.lock:
        out = wuxi_learn.review(job, result, by=_who(request))
        STORE.save(job)
    log_access(request, "review", jid, note=result)
    return JSONResponse({"ok": True, **out})


# ───────────────────────────────────────── 운영 화면 API
import wuxi_admin  # noqa: E402

wuxi_admin.install(app, STORE, sys.modules[__name__])


# ───────────────────────────────────────── 명령
def _ask_password(label: str) -> str:
    pw = getpass.getpass(f"{label}(8자 이상): ")
    if len(pw) < 8:
        sys.exit("8자 이상이어야 합니다")
    if getpass.getpass("한 번 더: ") != pw:
        sys.exit("두 번 넣은 값이 다릅니다")
    return pw


def _set_password(who: str = "user") -> None:
    label = "고객 화면 비밀번호" if who == "user" else "운영 화면 비밀번호"
    f = save_password(who, _ask_password(label))
    print(f"저장했습니다: {f} (해시만 들어 있습니다)")


def _start(host: str, port: int, open_browser: bool) -> None:
    """처음 쓰는 사람용. 빠진 것을 하나씩 묻고 확인한 뒤 띄운다."""
    import wuxi_admin
    print("우시 서류 판독을 준비합니다.\n")
    if stored_hash("admin") is None:
        print("운영 화면 비밀번호가 없습니다. 규칙과 학습을 관리할 때 씁니다.")
        _set_password("admin")
    if stored_hash("user") is None:
        print("고객 화면 비밀번호가 없습니다. 서류를 올리는 사람이 씁니다.")
        _set_password("user")
    st = wuxi_admin.claude_status(force=True)
    if not st.get("found"):
        print("\nClaude Code CLI 를 찾지 못했습니다. 설치한 뒤 다시 실행하십시오.")
        print("  Windows  : PowerShell 에서  irm https://claude.ai/install.ps1 | iex")
        print("  맥, 리눅스: curl -fsSL https://claude.ai/install.sh | bash")
        sys.exit(1)
    if not st.get("loggedIn"):
        print("\nClaude 에 로그인되어 있지 않습니다. 터미널에서  claude  를 실행해 자기 계정으로 로그인한 뒤 다시 실행하십시오.")
        sys.exit(1)
    who = st.get("email") or st.get("authMethod") or "로그인됨"
    print(f"\nClaude 계정: {who}  (CLI {st.get('version') or ''})")
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"
    print(f"고객 화면  {url}\n운영 화면  {url}admin\n끝내려면 Ctrl+C")
    if open_browser:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("set-password", help="고객 화면 비밀번호를 정한다")
    sub.add_parser("set-admin-password", help="운영 화면 비밀번호를 정한다")
    for name, help_ in (("serve", "혼자 띄운다"), ("start", "처음 쓰는 사람용: 준비를 확인하고 띄운 뒤 브라우저를 연다")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--host", default="127.0.0.1")
        s.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8767))
        if name == "start":
            s.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "set-password":
        _set_password("user")
        return
    if a.cmd == "set-admin-password":
        _set_password("admin")
        return
    if a.cmd == "start":
        _start(a.host, a.port, not a.no_browser)
        return
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
