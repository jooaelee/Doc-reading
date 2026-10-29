# -*- coding: utf-8 -*-
"""확인 화면을 띄우고 새 서류를 바로 뽑는 작은 서버. 개발자가 보게 CLI 호출 로그를 그대로 흘린다.

    python3 tools/ui_server.py            # http://127.0.0.1:8765/confirm.html

  GET  /confirm.html, /kerol_mock.html   생성된 화면 (work/out/ui)
  GET  /docs/<케이스>/<파일>.pdf         원본 서류 (항공, 해상 폴더에서 찾는다)
  GET  /uploads/<작업>/<파일>.pdf        새로 올린 서류
  POST /api/extract  (multipart pdf=...) 올린 PDF 로 추출을 시작하고 작업 번호를 돌려준다
  GET  /api/job/<작업>                   진행 단계와 결과
  GET  /api/log                          서버가 부른 CLI 명령과 걸린 시간

추출은 docpipe.run 을 그대로 쓴다. 호출마다 어떤 서류를 어느 명령으로 불렀는지 단계에 적는다.
"""
import sys, os, json, time, threading, uuid, re, io
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe, confirm_ui, wuxi_paths

UI_DIR = os.environ.get("UI_DIR") or wuxi_paths.work("out", "ui")
DOC_ROOTS = wuxi_paths.doc_roots()
UPLOADS = os.path.expanduser(os.environ.get("UPLOAD_DIR") or wuxi_paths.work("out", "uploads"))
CLAUDE_BIN = wuxi_paths.claude_bin()
CONFIG_DIR = wuxi_paths.claude_config_dir()
MODEL = os.environ.get("MODEL") or "claude-sonnet-5"
# 🔴 하이닉스 중국법인 판. 화면은 같은 것을 쓰고 추출 판과 카드 하나만 갈린다.
#    화면을 따로 만들면 고칠 때 둘이 갈라진다. 실제로 데모용으로 따로 만들어 봤더니
#    같은 고침을 두 곳에 해야 했다. 여기서는 갈림을 이 값 하나로 둔다.
HYNIX = os.environ.get("HYNIX", "") == "1"
PROMPT = "h1" if HYNIX else "v6"
CODES = os.path.join(wuxi_paths.SCHEMA, "codes.json")

JOBS = {}
LOG = []          # (시각, 글)
LOCK = threading.Lock()
_current = {"job": None}     # 한 번에 한 작업만 돈다. 호출은 작업 스레드 밖(스레드 풀)에서도 일어나므로 전역이다
JOB_LOCK = threading.Lock()


def log(text):
    with LOCK:
        LOG.append((time.strftime("%H:%M:%S"), text))
        del LOG[:-400]
    job = _current["job"]
    if job is not None:
        job["log"].append(text)


def step(job, text, done=False, sec=None):
    with LOCK:
        if done:
            # 서류별 호출이 동시에 도니 같은 글의 '진행 중' 항목을 뒤에서부터 찾아 닫는다
            for st in reversed(job["steps"]):
                if not st["done"] and st["text"] == text:
                    st.update(done=True, sec=sec); return
        job["steps"].append({"text": text, "done": done, "sec": sec})


# CLI 호출을 감싸 단계와 로그를 남긴다
_real_call = docpipe.call_claude_cli


def _kind_of(schema):
    keys = set((schema or docpipe.SCHEMA_STRICT).get("properties", {}))
    if "lca_lines" in keys and len(keys) <= 4: return "LCA"
    if "awb_pieces" in keys and "ci_lines" not in keys: return "AWB"
    if "ci_lines" in keys and "pl_lines" in keys and "awb_pieces" not in keys: return "CI+PL 이미지"
    if "ci_lines" in keys and "pl_lines" not in keys: return "CI"
    if "pl_lines" in keys and "ci_lines" not in keys: return "PL"
    return "전체 (스캔)"


def _logged_call(model, messages, paths=None, timeout=300, binary="claude", think=False, config_dir=None, schema=None):
    kind = _kind_of(schema)
    body = docpipe.usr_text(messages)
    n_pages = body.count("=== page ")
    job = _current["job"]
    n_img = sum(1 for x in (paths or []) if str(x).lower().endswith(".png"))
    text = f"{kind} {n_pages}쪽 추출" + (f" (이미지 {n_img})" if n_img else "")
    if job: step(job, text)
    log(f"$ {binary} -p --input-format stream-json --output-format stream-json --mcp-config <{kind} 스키마 {len((schema or docpipe.SCHEMA_STRICT)['properties'])}칸>"
        f" --allowed-tools mcp__ex__extract --model {model}  < 텍스트 {len(body):,}자" + (f", PNG {n_img}장" if n_img else ""))
    t0 = time.perf_counter()
    try:
        raw, st = _real_call(model, messages, paths=paths, timeout=timeout, binary=binary, think=think, config_dir=config_dir, schema=schema)
    except Exception as e:
        log(f"  실패 {time.perf_counter() - t0:.1f}초: {str(e)[:120]}")
        if job: step(job, text, done=True, sec=time.perf_counter() - t0)
        raise
    sec = time.perf_counter() - t0
    log(f"  {kind} {sec:.1f}초 | 값 {sum(1 for v in (raw or {}).values() if v not in (None, '', []))}칸")
    if job: step(job, text, done=True, sec=sec)
    return raw, st


docpipe.call_claude_cli = _logged_call


def run_job(job):
    with JOB_LOCK:
        _run_job(job)


def _run_job(job):
    _current["job"] = job
    t0 = time.perf_counter()
    try:
        pdfs = job["pdfs"]
        step(job, f"PDF {len(pdfs)}개 쪽 분류")
        pages = docpipe.apply_awb_sort([docpipe.classify(p) for path in pdfs for p in docpipe.load_pdf(path)])
        n_scan = sum(1 for p in pages if p.kind == "SCAN")
        if n_scan:
            t_ocr = time.perf_counter()
            d = docpipe.ocr_pages(pages); docpipe.route_scans(pages)
            if d: __import__("shutil").rmtree(d, ignore_errors=True)
            log(f"OCR {n_scan}쪽 {time.perf_counter() - t_ocr:.1f}초")
        kinds = " ".join(p.kind + ("(B/L)" if getattr(p, "sea", False) else "") + ("*" if p.image else "") for p in pages)
        step(job, f"PDF {len(pdfs)}개 쪽 분류" + (f", 스캔 {n_scan}쪽 OCR" if n_scan else ""), done=True, sec=time.perf_counter() - t0)
        log(f"쪽: {kinds}")
        names = {"AWB": "AWB", "CI": "CI", "PL": "PL", "CUSTOMS": "통관 서류", "TERMS": "약관", "OTHER": "기타", "SCAN": "미분류 스캔"}
        cnt = {}
        for p in pages:
            k = "B/L" if getattr(p, "sea", False) else names.get(p.kind, p.kind)
            cnt[k] = cnt.get(k, 0) + 1
        n_img = sum(1 for p in pages if p.image)
        step(job, f"전체 {len(pages)}쪽: " + ", ".join(f"{k} {v}쪽" for k, v in cnt.items()) + (f" (이미지 {n_img}쪽)" if n_img else ""), done=True, sec=0)
        forms = [f for f in (docpipe.parse_awb_form(p) or {} for p in pages if p.kind == "AWB" and not p.needs_image and not getattr(p, "sea", False)) if f]
        if forms:
            step(job, f"AWB 양식 {forms[0].get('_form')} 좌표 판독", done=True, sec=0)
        docpipe.set_prompt(PROMPT)
        t1 = time.perf_counter()
        rows = docpipe.run([(job["id"], pdfs)], CODES, model=MODEL, show=False, masters=docpipe.load_masters(),
                           claude_cli=True, claude_bin=CLAUDE_BIN, claude_config_dir=CONFIG_DIR)
        r = rows[0]
        step(job, "검증, KEROL 등록 행 생성", done=True, sec=0.0)
        if (r.get("llm") or {}).get("per_call"):
            log("  호출별: " + ", ".join(f"{c['kind']} {c['wall']}초" for c in r["llm"]["per_call"]))
        if r.get("llm", {}).get("error"):
            raise RuntimeError(r["llm"]["error"])
        rec = {"case": job["id"], "raw": r.get("raw"), "verify": r.get("verify"), "so_row": r.get("so_row"), "codes": r.get("codes"),
               "llm": r.get("llm"), "wall": round(time.perf_counter() - t1, 1)}
        with open(os.path.join(job["dir"], "_result.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        case = confirm_ui.build_case(rec, None, os.path.dirname(job["dir"]), docs_prefix="uploads", hynix=HYNIX)
        case["case"] = job["name"]
        job.update(status="done", case=case, wall=round(time.perf_counter() - t0, 1))
        usd = (r.get("llm") or {}).get("api_usd")
        if usd is not None:
            step(job, f"API 환산 비용 ${usd:.3f} (약 {round(usd * 1400)}원)", done=True, sec=0.0)
        log(f"작업 {job['id'][:8]} 완료: {job['wall']}초 | 호출 {(r.get('llm') or {}).get('calls')}회 | 오류 {case['fail']}"
            + (f" | API 환산 ${usd:.3f}" if usd is not None else ""))
    except Exception as e:
        msg = str(e)
        if re.search(r"ENOTFOUND|Can't reach the API|ECONNRESET|ETIMEDOUT|network", msg, re.I):
            msg = "모델 API 서버에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 추출하십시오."
        elif re.search(r"rate limit|usage limit|429", msg, re.I):
            msg = "모델 사용 한도에 걸렸습니다. 잠시 뒤 다시 추출하십시오."
        job.update(status="error", error=msg[:300], wall=round(time.perf_counter() - t0, 1))
        log(f"작업 {job['id'][:8]} 실패: {str(e)[:200]}")
    finally:
        _current["job"] = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI_DIR, **kw)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(os.path.getsize(path))); self.end_headers()
        with open(path, "rb") as f: self.wfile.write(f.read())

    def do_GET(self):
        from urllib.parse import unquote
        # 🔴 주소는 퍼센트 인코딩된 채 온다. 케이스 폴더 이름에 한글이 있으면 그대로는 파일을
        #    못 찾아 원본 PDF 창이 통째로 빈다(시험 서류 7건이 전부 한글 이름이었다).
        p = unquote(self.path.split("?")[0])
        if p.startswith("/docs/"):
            rel = os.path.normpath(p[len("/docs/"):]).replace("..", "")
            for root in DOC_ROOTS:
                full = os.path.join(root, rel)
                if os.path.isfile(full): return self._file(full)
            return self.send_error(404)
        if p.startswith("/assets/"):
            # pdf.js 를 우리가 낸다. 인터넷이 막힌 자리에서도 원본 창이 떠야 한다.
            f = os.path.join(wuxi_paths.work("out", "ui_assets"), os.path.basename(p))
            if os.path.isfile(f):
                with open(f, "rb") as fh: data = fh.read()
                self.send_response(200)
                # 글꼴도 여기서 낸다(시연 녹화의 자막 글꼴). 확장자로 형식을 정한다
                ctype = {".woff2": "font/woff2", ".css": "text/css; charset=utf-8"}.get(
                    os.path.splitext(f)[1], "application/javascript; charset=utf-8")
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            return self.send_error(404)
        if p.startswith("/uploads/"):
            rel = os.path.normpath(p[len("/uploads/"):]).replace("..", "")
            return self._file(os.path.join(UPLOADS, rel))
        if p.startswith("/api/job/"):
            job = JOBS.get(p.split("/")[-1])
            if not job: return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
            return self._json({k: job[k] for k in ("id", "status", "steps", "wall", "case", "error", "log") if k in job})
        if p == "/api/log":
            return self._json({"lines": LOG[-200:]})
        if p == "/":
            self.send_response(302); self.send_header("Location", "/confirm.html"); self.end_headers(); return
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/xlsx":
            # 화면에서 고친 값을 그대로 받는다. 고쳐도 안 담기면 확인 화면이 아니라 구경 화면이다.
            import gerp_xlsx
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or "{}") if n else {}
            # 화면에 있는 건 전부를 한 파일로 낸다. 한 건만 담으면 건마다 다시 눌러야 한다.
            cases = body.get("cases") or [body]
            t = {"head": [], "items": [], "inv": [], "review": [], "req": [], "core3": []}
            for c in cases:
                name = c.get("case") or "케이스"
                ex = c.get("ex") or {}
                rows = c.get("lines") or []
                v = c.get("verify") or []
                t["head"].append(gerp_xlsx.head_row(name, ex))
                t["items"].extend(rows)
                t["inv"].extend(c.get("inv") or gerp_xlsx.invoice_rows(name, ex))
                t["req"].extend(gerp_xlsx.req_rows(name, ex))
                t["core3"].append(gerp_xlsx.core3_rows(name, ex))
                t["review"].append(gerp_xlsx.review_row(name, {"verify": [x[:3] for x in v]}, rows))
            os.makedirs(UPLOADS, exist_ok=True)
            out = os.path.join(UPLOADS, "gerp.xlsx")
            gerp_xlsx.write_tables(t, out)
            with open(out, "rb") as f: data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        if self.path != "/api/extract":
            return self.send_error(404)
        items = wuxi_paths.upload_files(self, "pdf")  # cgi 는 파이썬 3.13 에서 없어졌다
        jid = uuid.uuid4().hex
        d = os.path.join(UPLOADS, jid); os.makedirs(d, exist_ok=True)
        pdfs = []
        for it in items:
            if not getattr(it, "filename", None): continue
            name = re.sub(r"[^A-Za-z0-9._\-가-힣]", "_", os.path.basename(it.filename)) or "doc.pdf"
            path = os.path.join(d, name)
            with open(path, "wb") as f: f.write(it.file.read())
            pdfs.append(path)
        if not pdfs: return self._json({"error": "PDF 파일이 없습니다"}, 400)
        job = {"id": jid, "name": "신규-" + jid[:6], "dir": d, "pdfs": pdfs, "status": "running", "steps": [], "log": [], "wall": None}
        JOBS[jid] = job
        log(f"작업 {jid[:8]} 시작: {', '.join(os.path.basename(x) for x in pdfs)}")
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return self._json({"job": jid})


def main():
    os.makedirs(UPLOADS, exist_ok=True)
    port = int(os.environ.get("PORT") or 8765)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"serving {UI_DIR} on http://127.0.0.1:{port}/confirm.html  (docs: {', '.join(DOC_ROOTS)})", flush=True)
    log("서버 시작")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
