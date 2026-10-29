"""운영 화면(/admin)의 API. 현황, 규칙, 학습, 이력, 설정. wuxi_web.app 에 붙는다.

들어오는 법
  운영 비밀번호(admin.json, WUXI_ADMIN_HASH)로 들어온 사람만 쓴다. 비밀번호가 아직 없고 이 PC 에서 연 경우(127.0.0.1)에만
  화면에서 처음 비밀번호를 정할 수 있다. 서버에서는 `python tools/wuxi_web.py set-admin-password` 로 정한다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_learn  # noqa: E402
import wuxi_paths  # noqa: E402
import wuxi_rules as R  # noqa: E402

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

NO_STORE = {"Cache-Control": "no-store"}
KIND_KO = {"AWB": "운송장", "CI": "인보이스", "PL": "포장명세서", "CUSTOMS": "통관 용도설명서", "TERMS": "약관",
           "OTHER": "기타", "SCAN": "스캔", "LCA": "운임표", "PO": "구매 주문서", "INTERNAL": "내부 문서"}
MODELS = [
    {"id": "claude-sonnet-5", "name": "Sonnet 5", "note": "기본값. 실제 서류와 가짜 묶음 시험을 마친 모델"},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "note": "최신 Sonnet. 바꾸면 설치 점검으로 먼저 확인"},
    {"id": "claude-opus-5-5", "name": "Opus 5.5", "note": "가장 정확하지만 느리고 사용량이 큼"},
    {"id": "claude-haiku-5-5", "name": "Haiku 5.5", "note": "빠르고 가벼움. 긴 표는 빠짐 검사로 꼭 확인"},
]
_claude_cache: dict = {"at": 0.0, "value": None}


# ───────────────────────────────────────── Claude 계정
def _env_for(config_dir):
    env = dict(os.environ)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def _account(config_dir) -> dict:
    """로그인한 계정의 이메일과 조직 이름(있을 때만). 비밀값은 읽지 않는다."""
    f = Path(config_dir) / ".claude.json" if config_dir else Path.home() / ".claude.json"
    try:
        acct = (json.loads(f.read_text(encoding="utf-8")) or {}).get("oauthAccount") or {}
    except (OSError, ValueError):
        return {}
    return {k: acct.get(src) for k, src in (("email", "emailAddress"), ("org", "organizationName"),
                                            ("name", "displayName")) if acct.get(src)}


def claude_status(force=False) -> dict:
    """Claude Code CLI 가 있는지, 어떤 계정으로 로그인했는지. 60초 동안 기억한다."""
    if not force and _claude_cache["value"] and time.time() - _claude_cache["at"] < 60:
        return dict(_claude_cache["value"])
    binp = wuxi_paths.claude_bin()
    cfg = wuxi_paths.claude_config_dir()
    found = bool(shutil.which(binp) or os.path.exists(binp))
    out = {"bin": binp, "found": found, "configDir": cfg or "", "configDirShown": cfg or "기본 계정(~/.claude)",
           "loggedIn": False, "version": "", "authMethod": "", "error": ""}
    if found:
        try:
            r = subprocess.run([binp, "--version"], capture_output=True, text=True, timeout=20, env=_env_for(cfg),
                               encoding="utf-8", errors="replace")
            out["version"] = (r.stdout or "").strip().split("\n")[0][:60]
            r = subprocess.run([binp, "auth", "status"], capture_output=True, text=True, timeout=30, env=_env_for(cfg),
                               encoding="utf-8", errors="replace")
            data = json.loads(r.stdout) if (r.stdout or "").strip().startswith("{") else {}
            out.update(loggedIn=bool(data.get("loggedIn")), authMethod=str(data.get("authMethod") or ""),
                       apiProvider=str(data.get("apiProvider") or ""))
            if not data:
                out["error"] = ((r.stderr or r.stdout or "").strip() or "로그인 상태를 읽지 못했습니다")[:200]
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            out["error"] = str(e)[:200]
    out.update(_account(cfg))
    _claude_cache.update(at=time.time(), value=out)
    return dict(out)


def claude_test(model: str) -> dict:
    """아주 짧은 질문 하나로 판독에 쓸 계정과 모델이 실제로 답하는지 본다."""
    binp, cfg = wuxi_paths.claude_bin(), wuxi_paths.claude_config_dir()
    t0 = time.time()
    try:
        r = subprocess.run([binp, "-p", "--model", model], input="Reply with the single word OK.", capture_output=True,
                           text=True, timeout=180, env=_env_for(cfg), encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "seconds": round(time.time() - t0, 1), "error": str(e)[:200]}
    reply = (r.stdout or "").strip()
    ok = r.returncode == 0 and "OK" in reply.upper()
    return {"ok": ok, "seconds": round(time.time() - t0, 1), "reply": reply[:80],
            "error": "" if ok else ((r.stderr or reply or f"끝 코드 {r.returncode}").strip()[:300])}


# ───────────────────────────────────────── 현황
def _kinds(p: dict) -> dict:
    cnt = {}
    for k in [x.get("kind") for x in p.get("pages") or []] or p.get("kinds") or []:
        cnt[KIND_KO.get(k, k)] = cnt.get(KIND_KO.get(k, k), 0) + 1
    return cnt


def job_row(job: dict, web) -> dict:
    p = job.get("payload") or {}
    rules = p.get("rules") or []
    ed = job.get("edits") or {}
    return {
        "id": job["id"], "created": job.get("created"), "status": job.get("status"), "error": job.get("error"),
        "files": web._files(job), "supplier": job.get("supplier_name") or "", "supplierId": job.get("supplier") or "",
        "kinds": _kinds(p), "pages": len(p.get("pages") or p.get("kinds") or []), "lines": len(p.get("items") or []),
        "verdict": (p.get("completeness") or {}).get("verdict"), "gaps": p.get("gaps") or 0,
        "ruleCells": sum(1 for r in rules if r.get("line") is not None),
        "promptRules": sum(1 for r in rules if r.get("kind") == "prompt"),
        "edited": ed.get("cells") or 0, "added": ed.get("added") or 0, "confirmed": bool(job.get("confirmed")),
        "downloads": job.get("downloads") or 0, "wall": job.get("wall"),
        "review": (job.get("review") or {}).get("result"),
    }


def overview(store, web, days: int) -> dict:
    store.load_all()
    cut = time.time() - days * 86400
    jobs = sorted((j for j in store.jobs.values() if (j.get("created") or 0) >= cut and j.get("status") != "deleted"),
                  key=lambda j: j.get("created") or 0, reverse=True)
    rows = [job_row(j, web) for j in jobs]
    done = [r for r in rows if r["status"] == "done"]
    kinds, sups = {}, {}
    for r in done:
        for k, v in r["kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
        if r["supplier"]:
            sups[r["supplier"]] = sups.get(r["supplier"], 0) + 1
    verdicts = {v: sum(1 for r in done if r["verdict"] == v) for v in ("이상 없음", "빠진 줄 의심", "확인 못 함")}
    walls = [r["wall"] for r in done if r["wall"]]
    return {
        "days": days,
        "summary": {"jobs": len(rows), "done": len(done), "working": sum(1 for r in rows if r["status"] in ("queued", "running")),
                    "error": sum(1 for r in rows if r["status"] == "error"), "lines": sum(r["lines"] for r in done),
                    "verdicts": verdicts, "edited": sum(1 for r in done if r["edited"] or r["added"]),
                    "confirmed": sum(1 for r in done if r["confirmed"]), "ruleCells": sum(r["ruleCells"] for r in done),
                    "avgWall": round(sum(walls) / len(walls), 1) if walls else None,
                    "kinds": kinds, "suppliers": sorted(sups.items(), key=lambda x: -x[1])[:12]},
        "jobs": rows[:300],
        "rules": R.counts(),
    }


def job_detail(job: dict, web) -> dict:
    p = job.get("payload") or {}
    rules = []
    for t in p.get("rules") or []:
        r = R.get(t["rule"]) or {}
        rules.append({**t, "text": R.describe(r) if r else "(지워진 규칙)", "scope": R.supplier_name(r.get("scope")) if r else "",
                      "fieldName": R.FIELDS.get(t.get("field"), t.get("field") or ""),
                      "row": None if t.get("line") is None else t["line"] + 1})
    pages = []
    for pg in p.get("pages") or []:
        pages.append({**pg, "kindName": KIND_KO.get(pg.get("kind"), pg.get("kind"))})
    return {
        **job_row(job, web), "steps": job.get("steps") or [], "shipper": p.get("shipper") or "",
        "pageList": pages, "completeness": p.get("completeness") or {}, "rules": rules,
        "edits": (job.get("edits") or {}).get("detail") or [], "editSummary": job.get("edits") or {},
        "reviewInfo": job.get("review") or {}, "counts": p.get("counts") or {}, "llm": p.get("llm") or {},
        "inv": p.get("inv") or [], "required": p.get("required") or [],
    }


# ───────────────────────────────────────── 학습
def learn(store) -> dict:
    store.load_all()
    jobs = [j for j in store.jobs.values() if j.get("status") == "done"]
    cands = []
    for r in R.all_rules():
        if r["status"] != "candidate":
            continue
        ev = r.get("evidence") or []
        jobs_n = len({e.get("job") for e in ev})
        cands.append({**r, "text": R.describe(r), "kindName": R.KINDS[r["kind"]], "scopeName": R.supplier_name(r["scope"]),
                      "evidenceJobs": jobs_n, "evidenceCount": len(ev), "evidence": ev[-5:][::-1],
                      "preview": wuxi_learn.preview(r, jobs)})
    cands.sort(key=lambda c: (-c["evidenceJobs"], -(c.get("updated_evidence") or c.get("updated") or 0)))
    fb = wuxi_learn.feedback(30)
    recent = [h for h in R.history(60) if h.get("event") in ("create", "activate", "promote", "off", "reject")][:8]
    for h in recent:
        h["eventName"] = R.EVENTS.get(h.get("event"), h.get("event"))
        h["scopeName"] = R.supplier_name(h.get("scope")) if h.get("scope") else ""
    return {
        "candidates": cands,
        "promotions": wuxi_learn.promotions(),
        "feedback": {"cells": sum(1 for f in fb if f.get("type") == "cell"),
                     "added": sum(f.get("n", 0) for f in fb if f.get("type") == "added"),
                     "reviewsOk": sum(1 for f in fb if f.get("type") == "review" and f.get("result") == "ok"),
                     "reviewsMissing": sum(1 for f in fb if f.get("type") == "review" and f.get("result") == "missing"),
                     "confirms": sum(1 for f in fb if f.get("type") == "confirm")},
        "recent": recent,
    }


def rules_view(store) -> dict:
    store.load_all()
    sups = R.suppliers()
    jobs_by = {}
    for j in store.jobs.values():
        if j.get("supplier"):
            jobs_by[j["supplier"]] = jobs_by.get(j["supplier"], 0) + 1
    rules = []
    for r in R.all_rules():
        if r["status"] == "rejected":
            continue
        rules.append({**r, "text": R.describe(r), "kindName": R.KINDS[r["kind"]], "scopeName": R.supplier_name(r["scope"]),
                      "statusName": R.STATUS.get(r["status"], r["status"]),
                      "promotedFromName": R.supplier_name(r["promoted_from"]) if r.get("promoted_from") else ""})
    per = {}
    for r in rules:
        if r["status"] in ("active", "candidate"):
            per.setdefault(r["scope"], {"active": 0, "candidate": 0})[r["status"]] += 1
    sup_rows = [{"id": sid, "name": s.get("name"), "match": s.get("match") or [], "note": s.get("note") or "",
                 "source": s.get("source"), "jobs": jobs_by.get(sid, 0), **per.get(sid, {"active": 0, "candidate": 0})}
                for sid, s in sorted(sups.items(), key=lambda x: (-jobs_by.get(x[0], 0), x[1].get("name") or ""))]
    return {"suppliers": sup_rows, "common": per.get(R.COMMON, {"active": 0, "candidate": 0}), "rules": rules,
            "builtinHeaders": R.BUILTIN_HEADERS, "fields": R.FIELDS, "codeFields": list(R.CODE_FIELDS),
            "headerFields": list(R.HEADER_FIELDS), "aliasFields": list(R.ALIAS_FIELDS),
            "checks": {k: {"name": v[0], "desc": v[1]} for k, v in R.CHECKS.items()}, "gerpColumns": R.GERP_COLUMNS,
            "counts": R.counts()}


# ───────────────────────────────────────── 붙이기
def install(app, store, web) -> None:
    """wuxi_web 의 app 에 /api/admin/* 를 붙인다. web 은 wuxi_web 모듈(비밀번호, 쿠키, 기록 함수를 빌린다)."""

    def need(request: Request):
        return None if web._admin(request) is not None else JSONResponse({"error": "운영 비밀번호로 들어와 주십시오.", "login": True}, 401)

    async def body_of(request: Request) -> dict:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def local(request: Request) -> bool:
        return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")

    def cookie_path(request: Request) -> str:
        return (request.scope.get("root_path") or "") + "/"

    @app.get("/api/admin/state")
    def a_state(request: Request):
        name = web._admin(request)
        return JSONResponse({"auth": name is not None, "name": name or "", "configured": web.stored_hash("admin") is not None,
                             "setup": web.stored_hash("admin") is None and local(request),
                             "userConfigured": web.stored_hash("user") is not None}, headers=NO_STORE)

    @app.post("/api/admin/setup")
    async def a_setup(request: Request):
        if web.stored_hash("admin") is not None:
            return JSONResponse({"error": "운영 비밀번호가 이미 정해져 있습니다."}, 409)
        if not local(request):
            return JSONResponse({"error": "처음 설정은 이 PC 에서 연 화면(127.0.0.1)에서만 할 수 있습니다."}, 403)
        b = await body_of(request)
        try:
            web.save_password("admin", str(b.get("admin") or ""))
            if b.get("user"):
                web.save_password("user", str(b.get("user")))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        name = str(b.get("name") or "")[:30]
        r = JSONResponse({"ok": True})
        r.set_cookie(web.ADMIN_COOKIE, web.make_cookie("admin", name), max_age=web.SESSION_HOURS * 3600, httponly=True,
                     samesite="lax", path=cookie_path(request))
        R._history("settings", by=name or "관리자", extra={"text": "운영 화면 비밀번호를 처음 정했습니다"})
        return r

    @app.post("/api/admin/login")
    async def a_login(request: Request):
        ip = request.client.host if request.client else ""
        if web.stored_hash("admin") is None:
            return JSONResponse({"error": "운영 비밀번호가 아직 없습니다."}, 503)
        if web._too_many(ip):
            return JSONResponse({"error": "비밀번호를 여러 번 틀렸습니다. 1분 뒤 다시 해 주십시오."}, 429)
        b = await body_of(request)
        if not web.check_password(str(b.get("password") or "")[:200], web.stored_hash("admin")):
            web._failed(ip)
            web.log_access(request, "admin-login", status=401)
            return JSONResponse({"error": "비밀번호가 맞지 않습니다."}, 401)
        name = re.sub(r"[^\w가-힣 .-]", "", str(b.get("name") or ""))[:30]
        web.log_access(request, "admin-login", note=name)
        r = JSONResponse({"ok": True})
        r.set_cookie(web.ADMIN_COOKIE, web.make_cookie("admin", name), max_age=web.SESSION_HOURS * 3600, httponly=True,
                     samesite="lax", path=cookie_path(request))
        return r

    @app.post("/api/admin/logout")
    def a_logout(request: Request):
        r = JSONResponse({"ok": True})
        r.delete_cookie(web.ADMIN_COOKIE, path=cookie_path(request))
        return r

    @app.get("/api/admin/overview")
    def a_overview(request: Request, days: int = 14):
        return need(request) or JSONResponse(overview(store, web, max(1, min(365, days))), headers=NO_STORE)

    @app.get("/api/admin/jobs/{jid}")
    def a_job(jid: str, request: Request):
        if (r := need(request)):
            return r
        job = store.get(jid)
        if not job:
            return JSONResponse({"error": "작업을 찾을 수 없습니다."}, 404)
        return JSONResponse(job_detail(job, web), headers=NO_STORE)

    @app.get("/api/admin/rules")
    def a_rules(request: Request):
        return need(request) or JSONResponse(rules_view(store), headers=NO_STORE)

    @app.post("/api/admin/rules")
    async def a_rule_add(request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        try:
            rule = R.add(str(b.get("kind") or ""), str(b.get("scope") or R.COMMON), b.get("data") or {},
                         by=web._who(request), reason=str(b.get("reason") or ""))
        except (ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "rule": rule, "text": R.describe(rule)})

    @app.post("/api/admin/rules/{rid}/{action}")
    async def a_rule_change(rid: str, action: str, request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        try:
            rule = R.change(rid, action, by=web._who(request), reason=str(b.get("reason") or ""), data=b.get("data"))
            if action == "activate" and b.get("scope") == R.COMMON and rule["scope"] != R.COMMON:
                rule = R.change(rid, "promote", by=web._who(request), reason=str(b.get("reason") or "후보를 공통으로 반영"))
        except KeyError as e:
            return JSONResponse({"error": str(e).strip("'")}, 404)
        except (ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "rule": rule, "text": R.describe(rule)})

    @app.post("/api/admin/suppliers")
    async def a_supplier(request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        match = b.get("match")
        if isinstance(match, str):
            match = [w.strip() for w in re.split(r"[,\n]", match) if w.strip()]
        try:
            s = R.save_supplier(str(b.get("name") or ""), match, str(b.get("note") or ""), b.get("id") or None,
                                by=web._who(request))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "supplier": s})

    @app.get("/api/admin/learn")
    def a_learn(request: Request):
        return need(request) or JSONResponse(learn(store), headers=NO_STORE)

    @app.get("/api/admin/history")
    def a_history(request: Request, event: str = ""):
        if (r := need(request)):
            return r
        ev = [e for e in event.split(",") if e] or None
        rows = R.history(500, ev)
        for h in rows:
            h["eventName"] = R.EVENTS.get(h.get("event"), h.get("event"))
            h["scopeName"] = R.supplier_name(h.get("scope")) if h.get("scope") else ""
        return JSONResponse({"rows": rows}, headers=NO_STORE)

    @app.get("/api/admin/settings")
    def a_settings(request: Request, refresh: int = 0):
        if (r := need(request)):
            return r
        st = wuxi_paths.settings()
        return JSONResponse({
            "claude": claude_status(force=bool(refresh)), "model": wuxi_paths.model(), "models": MODELS,
            "keepDays": web.keep_days(), "configDir": st.get("claude_config_dir") or "", "claudeBin": st.get("claude_bin") or "",
            "passwords": {"user": web.stored_hash("user") is not None, "admin": web.stored_hash("admin") is not None},
            "paths": {"base": str(wuxi_paths.BASE), "work": str(wuxi_paths.WORK)}, "version": _version(),
        }, headers=NO_STORE)

    @app.post("/api/admin/settings")
    async def a_settings_save(request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        patch, notes = {}, []
        if "model" in b:
            m = str(b.get("model") or "").strip()
            if m and not re.fullmatch(r"claude-[a-z0-9.-]{2,40}", m):
                return JSONResponse({"error": "모델 이름이 맞지 않습니다."}, 400)
            patch["model"] = m
            notes.append(f"판독 모델 {m or '기본값'}")
        if "keepDays" in b:
            try:
                d = int(b.get("keepDays"))
            except (TypeError, ValueError):
                return JSONResponse({"error": "보관 일수는 숫자로 넣어 주십시오."}, 400)
            if not 1 <= d <= 365:
                return JSONResponse({"error": "보관 일수는 1일에서 365일 사이입니다."}, 400)
            patch["keep_days"] = d
            notes.append(f"보관 {d}일")
        for k, key in (("configDir", "claude_config_dir"), ("claudeBin", "claude_bin")):
            if k in b:
                v = str(b.get(k) or "").strip()
                patch[key] = v
                notes.append(("Claude 계정 폴더 " if k == "configDir" else "Claude CLI 위치 ") + (v or "기본값"))
        wuxi_paths.save_settings(patch)
        _claude_cache.update(at=0.0, value=None)
        R._history("settings", by=web._who(request), reason=str(b.get("reason") or ""), extra={"text": ", ".join(notes)})
        return JSONResponse({"ok": True})

    @app.post("/api/admin/claude-test")
    def a_claude_test(request: Request):
        return need(request) or JSONResponse(claude_test(wuxi_paths.model()))

    @app.post("/api/admin/password")
    async def a_password(request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        who = b.get("who")
        if who not in ("user", "admin"):
            return JSONResponse({"error": "어느 비밀번호인지 골라 주십시오."}, 400)
        try:
            web.save_password(who, str(b.get("password") or ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        R._history("settings", by=web._who(request),
                   extra={"text": ("고객 화면" if who == "user" else "운영 화면") + " 비밀번호를 바꿨습니다"})
        out = JSONResponse({"ok": True, "relogin": who == "admin"})
        if who == "admin":
            name = web._admin(request) or ""
            out.set_cookie(web.ADMIN_COOKIE, web.make_cookie("admin", name), max_age=web.SESSION_HOURS * 3600,
                           httponly=True, samesite="lax", path=cookie_path(request))
        return out

    @app.get("/api/admin/export")
    def a_export(request: Request):
        if (r := need(request)):
            return r
        data = json.dumps(R.export(), ensure_ascii=False, indent=1).encode("utf-8")
        name = time.strftime("wuxi-rules-%Y%m%d-%H%M.json")
        return Response(data, media_type="application/json",
                        headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/admin/import")
    async def a_import(request: Request):
        if (r := need(request)):
            return r
        b = await body_of(request)
        try:
            out = R.import_(b.get("data") or {}, by=web._who(request), reason=str(b.get("reason") or ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, **out})


def _version() -> str:
    """저장소의 커밋(있으면). 화면 아래에 띄워 어떤 판이 돌고 있는지 보인다."""
    try:
        r = subprocess.run(["git", "-C", str(wuxi_paths.BASE), "log", "-1", "--format=%h %cd", "--date=short"],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
