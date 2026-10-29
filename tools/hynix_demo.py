# -*- coding: utf-8 -*-
"""하이닉스 중국법인 데모. 서류를 넣으면 품목 줄을 뽑아 보여 주고, 고친 값 그대로 엑셀로 낸다.

    python3 tools/hynix_demo.py            # http://127.0.0.1:8766

🔴 왜 따로 띄우나. 우리 확인 화면(`tools/ui_server.py`)은 KEROL SO 한 행을 확인하는 화면이다.
   하이닉스가 보는 자리는 GERP 품목 표이고, 관심이 줄 단위 값에 있다. 같은 추출 결과라도
   보여 주는 자리가 다르면 상대는 자기 일과 연결을 못 짓는다.

🔴 화면이 지켜야 할 것 셋.
   1. 뽑은 값을 다 보여 준다. 필수와 선택으로 접지 않는다. 그쪽이 쓰는 값이 우리 기준의
      「선택」 자리에 많다.
   2. 사람이 고칠 수 있다. 고친 값이 그대로 엑셀로 나간다. 고쳐도 안 담기면 확인 화면이
      아니라 구경 화면이다.
   3. 어느 값이 서류 어디에서 왔는지 짚어 준다. 줄을 누르면 원본 그 자리에 상자가 뜬다.
      상대가 「이게 맞나」 를 묻는 방법이 그것 말고 없다.

키면 바로 돌게 표본 서류를 미리 걸어 둔다(`SAMPLES`). 업로드도 받는다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다.")

import argparse, io, json, os, re, shutil, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe, gerp_xlsx, wuxi_paths

ROOT = wuxi_paths.BASE
CODES = os.path.join(wuxi_paths.SCHEMA, "codes.json")
DOC_ROOTS = wuxi_paths.doc_roots()
WORK = os.path.abspath(os.path.expanduser(os.environ.get("HYNIX_WORK") or wuxi_paths.work("out", "hynix_demo")))
CLAUDE_BIN = wuxi_paths.claude_bin()
CONFIG_DIR = wuxi_paths.claude_config_dir()
MODEL = os.environ.get("MODEL") or "claude-sonnet-5"

# 켜자마자 누를 수 있게 걸어 두는 표본. 없는 것은 목록에서 빠진다.
# 🔴 케이스 번호(BL 번호)가 들어가서 저장소 밖 파일에서 읽는다. 모양: {"samples": [["케이스 폴더 이름", "설명"], ...]}
#    실제로 돌려 보고 적은 표본만 둔다. 안 돌려 본 것을 두면 데모 자리에서 처음 돌게 된다.
SAMPLES_FILE = os.environ.get("HYNIX_SAMPLES") or os.path.join(WORK, "samples.json")


def _samples():
    try:
        with open(SAMPLES_FILE, encoding="utf-8") as fh:
            return [tuple(x) for x in json.load(fh).get("samples", [])]
    except (OSError, ValueError):
        return []


SAMPLES = _samples()

JOBS = {}
JOB_LOCK = threading.Lock()
_cur = {"job": None}


# ---------- 표본 찾기 ----------
def sample_list():
    out = []
    for name, note in SAMPLES:
        for root in DOC_ROOTS:
            d = os.path.join(root, name)
            if os.path.isdir(d):
                pdfs = sorted(x for x in os.listdir(d) if x.lower().endswith(".pdf"))
                if pdfs:
                    out.append({"name": name, "note": note, "dir": d, "pdfs": pdfs})
                break
    return out


# ---------- 추출 ----------
def step(job, text, done=False, sec=None):
    if done:
        for st in reversed(job["steps"]):
            if not st["done"] and st["text"] == text:
                st.update(done=True, sec=round(sec or 0, 1)); return
    job["steps"].append({"text": text, "done": done, "sec": round(sec, 1) if sec else None})


_real_call = docpipe.call_claude_cli


def _logged_call(model, messages, paths=None, timeout=300, binary="claude", think=False,
                 config_dir=None, schema=None):
    job = _cur["job"]
    keys = set((schema or docpipe.SCHEMA_STRICT).get("properties", {}))
    kind = ("운송장" if "awb_pieces" in keys and "ci_lines" not in keys else
            "인보이스" if "ci_lines" in keys and "pl_lines" not in keys else
            "포장명세서" if "pl_lines" in keys and "ci_lines" not in keys else
            "인보이스와 포장명세서" if "ci_lines" in keys else "스캔 전체")
    n_img = sum(1 for x in (paths or []) if str(x).lower().endswith(".png"))
    text = f"{kind} 읽기" + (f" (그림 {n_img}쪽)" if n_img else "")
    if job: step(job, text)
    t0 = time.perf_counter()
    try:
        return _real_call(model, messages, paths=paths, timeout=timeout, binary=binary,
                          think=think, config_dir=config_dir, schema=schema)
    finally:
        if job: step(job, text, done=True, sec=time.perf_counter() - t0)


docpipe.call_claude_cli = _logged_call


def run_job(job):
    with JOB_LOCK:
        _cur["job"] = job
        t0 = time.perf_counter()
        try:
            docpipe.set_prompt("h1")
            step(job, f"서류 {len(job['pdfs'])}개 쪽 가르기")
            pages = docpipe.apply_awb_sort([docpipe.classify(p)
                                            for path in job["pdfs"] for p in docpipe.load_pdf(path)])
            n_scan = sum(1 for p in pages if p.kind == "SCAN")
            if n_scan:
                d = docpipe.ocr_pages(pages); docpipe.route_scans(pages)
                if d: shutil.rmtree(d, ignore_errors=True)
            names = {"AWB": "운송장", "CI": "인보이스", "PL": "포장명세서", "CUSTOMS": "통관 서류",
                     "TERMS": "약관", "OTHER": "기타", "SCAN": "스캔", "LCA": "운임표"}
            cnt = {}
            for p in pages:
                cnt.setdefault(names.get(p.kind, p.kind), 0)
                cnt[names.get(p.kind, p.kind)] += 1
            step(job, f"서류 {len(job['pdfs'])}개 쪽 가르기", done=True, sec=time.perf_counter() - t0)
            step(job, "전체 %d쪽: " % len(pages) + ", ".join(f"{k} {v}쪽" for k, v in cnt.items())
                 + (f" (스캔 {n_scan}쪽은 OCR 로 먼저 읽음)" if n_scan else ""), done=True, sec=0)
            rows = docpipe.run([(job["name"], job["pdfs"])], CODES, model=MODEL, show=False,
                               masters=docpipe.load_masters(), claude_cli=True,
                               claude_bin=CLAUDE_BIN, claude_config_dir=CONFIG_DIR)
            r = rows[0]
            if (r.get("llm") or {}).get("error"):
                raise RuntimeError(r["llm"]["error"])
            step(job, "숫자 검증과 표 만들기", done=True, sec=0)
            job["payload"] = build_payload(job, r)
            usd = (r.get("llm") or {}).get("api_usd")
            if usd is not None:
                step(job, f"API 환산 비용 ${usd:.3f} (약 {round(usd * 1400):,}원)", done=True, sec=0)
            job.update(status="done", wall=round(time.perf_counter() - t0, 1))
        except Exception as e:
            msg = str(e)
            if re.search(r"rate limit|usage limit|429", msg, re.I):
                msg = "모델 사용 한도에 걸렸습니다. 잠시 뒤 다시 누르십시오."
            elif re.search(r"ENOTFOUND|ECONNRESET|ETIMEDOUT|network", msg, re.I):
                msg = "모델 서버에 연결하지 못했습니다. 인터넷 연결을 확인하십시오."
            job.update(status="error", error=msg[:300], wall=round(time.perf_counter() - t0, 1))
        finally:
            _cur["job"] = None


# ---------- 화면에 줄 값 ----------
def find_rects(docs, text):
    """이 값이 서류 어디에 찍혀 있는지. [[쪽, x0, y0, x1, y1], ...] 최대 넷."""
    q = str(text or "").strip()
    if len(q) < 3:
        return []
    hits = []
    for di, doc in enumerate(docs):
        if doc is None: continue
        for pi, page in enumerate(doc):
            try: rects = page.search_for(q)
            except Exception: rects = []
            for r in rects[:2]:
                hits.append([di, pi + 1, round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)])
            if len(hits) >= 4: return hits
    return hits


def build_payload(job, r):
    import pymupdf
    ex = r.get("ex") or docpipe.postprocess(r.get("raw") or {})
    rec = {"case": job["name"], "ex": ex, "verify": r.get("verify") or []}
    t = gerp_xlsx.tables_for([rec])
    docs = []
    for p in job["pdfs"]:
        try: docs.append(pymupdf.open(p))
        except Exception: docs.append(None)
    sizes = [[[round(pg.rect.width), round(pg.rect.height)] for pg in d] if d else [] for d in docs]

    # 줄을 누르면 원본 어디를 봐야 하는지. PO 번호와 품목 코드와 금액 순으로 찾는다.
    for row in t["items"]:
        marks = []
        for col in ("Purchase Order", "Material", "Vendor Part No.", "Invoice amount"):
            for h in find_rects(docs, row.get(col)):
                marks.append(h)
            if marks: break
        row["_hl"] = marks[:4]
    for d in docs:
        if d: d.close()

    v = r.get("verify") or []
    checks = [{"등급": g, "항목": k, "말": m} for g, k, m in v]
    return {
        "case": job["name"],
        "head": t["head"][0] if t["head"] else {},
        "items": t["items"],
        "inv": t["inv"],
        "review": t["review"][0] if t["review"] else {},
        "checks": checks,
        "counts": {"OK": sum(1 for c in checks if c["등급"] == "OK"),
                   "WARN": sum(1 for c in checks if c["등급"] == "WARN"),
                   "FAIL": sum(1 for c in checks if c["등급"] == "FAIL")},
        "cols": {"gerp": gerp_xlsx.GERP_ITEM_COLS, "gerp_only": gerp_xlsx.GERP_ONLY,
                 "extra": gerp_xlsx.EXTRA_ITEM_COLS, "head": gerp_xlsx.GERP_HEAD_COLS,
                 "inv": gerp_xlsx.INV_COLS},
        "pdfs": [f"/pdf/{job['id']}/{i}" for i in range(len(job["pdfs"]))],
        "sizes": sizes,
        "llm": {"calls": (r.get("llm") or {}).get("calls"),
                "usd": (r.get("llm") or {}).get("api_usd")},
    }


# ---------- 화면 ----------
PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>서류에서 GERP 품목 표 만들기</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--dim:#6b7280;--line:#e5e7eb;--head:#f8f9fb;--accent:#1d4ed8;
      --ok:#15803d;--warn:#b45309;--fail:#b91c1c;--gap:#fef3c7;--gerp:#f1f2f4;--hl:rgba(253,186,64,.45)}
*{box-sizing:border-box}
body{margin:0;font:13px/1.55 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:var(--fg);background:var(--bg)}
header{display:flex;align-items:baseline;gap:14px;padding:10px 16px;border-bottom:1px solid var(--line)}
header b{font-size:15px}header span{color:var(--dim);font-size:12px}
.wrap{display:grid;grid-template-columns:230px 1fr 420px;height:calc(100vh - 43px)}
.col{overflow:auto;padding:12px 14px}
.col+.col{border-left:1px solid var(--line)}
h3{font-size:12px;color:var(--dim);margin:14px 0 6px;font-weight:600;letter-spacing:.02em}
h3:first-child{margin-top:0}
button{font:inherit;padding:6px 11px;border:1px solid var(--line);border-radius:6px;background:#fff;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button.pri{background:var(--accent);color:#fff;border-color:var(--accent)}
button.pri:hover{opacity:.9;color:#fff}
button[disabled]{opacity:.45;cursor:default}
.samp{display:block;width:100%;text-align:left;margin-bottom:6px;padding:8px 10px;line-height:1.4}
.samp b{display:block;font-size:12.5px}.samp i{font-style:normal;color:var(--dim);font-size:11.5px}
.steps{list-style:none;margin:0;padding:0;font-size:12px}
.steps li{padding:3px 0;color:var(--dim)}
.steps li.done{color:var(--fg)}
.steps li:before{content:"◦ ";color:var(--dim)}
.steps li.done:before{content:"● ";color:var(--ok)}
.sec{float:right;color:var(--dim);font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;white-space:nowrap}
th{background:var(--head);font-weight:600;position:sticky;top:0;z-index:2}
td[contenteditable]{cursor:text}
td[contenteditable]:focus{outline:2px solid var(--accent);outline-offset:-2px;background:#eff6ff}
td.gerp{background:var(--gerp);color:var(--dim)}
tr.gap td{background:var(--gap)}
tr.sel td{box-shadow:inset 0 0 0 9999px rgba(29,78,216,.07)}
tr.edited td:first-child:before{content:"✎ ";color:var(--accent)}
.scroll{overflow:auto;max-height:52vh;border:1px solid var(--line);border-radius:6px}
.kv{width:100%;font-size:12px}
.kv td:first-child{color:var(--dim);width:42%}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11.5px;margin-right:4px}
.ok{background:#dcfce7;color:var(--ok)}.warn{background:#fef3c7;color:var(--warn)}.fail{background:#fee2e2;color:var(--fail)}
.chk{font-size:12px;border-collapse:collapse;width:100%}
.chk td{border:0;border-bottom:1px solid var(--line);padding:4px 2px;white-space:normal;vertical-align:top}
.chk td:first-child{width:46px}
.pg{position:relative;margin-bottom:10px;border:1px solid var(--line)}
.pg canvas{display:block;width:100%;height:auto}
.hl{position:absolute;background:var(--hl);border:1px solid rgba(200,150,0,.7);border-radius:2px;pointer-events:none}
.note{color:var(--dim);font-size:11.5px;line-height:1.5;margin:6px 0 0}
.bar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.err{color:var(--fail);font-size:12px;white-space:pre-wrap}
</style></head><body>
<header><b>서류에서 GERP 품목 표 만들기</b>
<span id="sub">서류를 고르면 인보이스와 포장명세서의 품목 줄을 뽑습니다.</span></header>
<div class="wrap">
 <div class="col">
  <h3>표본 서류</h3><div id="samples"></div>
  <h3>직접 넣기</h3>
  <input type="file" id="file" accept="application/pdf" multiple style="font-size:11.5px;width:100%">
  <button id="up" style="margin-top:6px;width:100%">올린 서류로 뽑기</button>
  <h3>진행</h3><ul class="steps" id="steps"></ul>
  <div class="err" id="err"></div>
 </div>
 <div class="col">
  <div class="bar">
   <button class="pri" id="xlsx" disabled>엑셀로 내려받기</button>
   <span id="sum" class="note"></span>
  </div>
  <h3>품목 줄 (GERP B/L Item Update)</h3>
  <div class="scroll"><table id="items"></table></div>
  <p class="note" id="legend"></p>
  <h3>인보이스별 합계</h3><table id="inv"></table>
 </div>
 <div class="col">
  <h3>선적 헤더 (GERP B/L 헤더)</h3><table class="kv" id="head"></table>
  <h3>숫자 검증</h3><div id="pills"></div><table class="chk" id="checks"></table>
  <h3>원본 서류</h3><div id="pdf"></div>
 </div>
</div>
<script>
pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
let P=null, JOB=null, docsRendered=null, timer=null;
const $=s=>document.querySelector(s), esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

fetch('/api/samples').then(r=>r.json()).then(d=>{
  $('#samples').innerHTML = d.samples.length ? '' : '<p class="note">표본 서류가 없습니다. 오른쪽에서 직접 넣으십시오.</p>';
  d.samples.forEach(s=>{
    const b=document.createElement('button'); b.className='samp';
    b.innerHTML='<b>'+esc(s.name)+'</b><i>'+esc(s.note)+'</i>';
    b.onclick=()=>start('/api/extract-sample?name='+encodeURIComponent(s.name),null,b);
    $('#samples').appendChild(b);
  });
});

$('#up').onclick=()=>{
  const f=$('#file').files; if(!f.length){alert('PDF 를 고르십시오.');return;}
  const fd=new FormData(); for(const x of f) fd.append('pdf',x);
  start('/api/extract',fd,$('#up'));
};

function start(url,body,btn){
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  $('#err').textContent=''; $('#steps').innerHTML=''; $('#sum').textContent='';
  clearInterval(timer);
  fetch(url,body?{method:'POST',body}:{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error){fail(d.error);return;}
    JOB=d.job; timer=setInterval(poll,700); poll();
  }).catch(e=>fail(String(e)));
}
function fail(m){$('#err').textContent=m; document.querySelectorAll('button').forEach(b=>b.disabled=false); $('#xlsx').disabled=!P;}
function poll(){
  fetch('/api/job/'+JOB).then(r=>r.json()).then(d=>{
    $('#steps').innerHTML=(d.steps||[]).map(s=>'<li class="'+(s.done?'done':'')+'">'+esc(s.text)+
      (s.sec?'<span class="sec">'+s.sec+'초</span>':'')+'</li>').join('');
    if(d.status==='running') return;
    clearInterval(timer);
    document.querySelectorAll('button').forEach(b=>b.disabled=false);
    if(d.status==='error'){fail(d.error||'실패했습니다.');return;}
    P=d.payload; render();
  });
}

function render(){
  const c=P.cols, cols=c.gerp.concat(c.extra);
  let h='<thead><tr>'+cols.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>';
  P.items.forEach((r,i)=>{
    const gap=!String(r['Purchase Order']||'').trim()||(!String(r['Material']||'').trim()&&!String(r['Vendor Part No.']||'').trim());
    h+='<tr data-i="'+i+'" class="'+(gap?'gap':'')+'">'+cols.map(x=>{
      const only=c.gerp_only.indexOf(x)>=0;
      return '<td class="'+(only?'gerp':'')+'" data-c="'+esc(x)+'" contenteditable="true">'+esc(r[x])+'</td>';
    }).join('')+'</tr>';
  });
  $('#items').innerHTML=h+'</tbody>';
  $('#items').querySelectorAll('td').forEach(td=>{
    td.oninput=()=>{const tr=td.parentNode; P.items[+tr.dataset.i][td.dataset.c]=td.textContent.trim(); tr.classList.add('edited');};
    td.onfocus=()=>select(+td.parentNode.dataset.i);
  });
  $('#items').querySelectorAll('tr[data-i]').forEach(tr=>tr.onclick=()=>select(+tr.dataset.i));

  $('#inv').innerHTML='<thead><tr>'+c.inv.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+
    P.inv.map(r=>'<tr>'+c.inv.map(x=>'<td>'+esc(r[x])+'</td>').join('')+'</tr>').join('')+'</tbody>';
  $('#head').innerHTML=c.head.filter(x=>x!=='케이스').map(x=>
    '<tr><td>'+esc(x)+'</td><td>'+(String(P.head[x]||'').trim()?esc(P.head[x]):'<span style="color:#9ca3af">서류에 없음</span>')+'</td></tr>').join('');
  const n=P.counts;
  $('#pills').innerHTML='<span class="pill ok">맞음 '+n.OK+'</span><span class="pill warn">확인 '+n.WARN+'</span><span class="pill fail">어긋남 '+n.FAIL+'</span>';
  $('#checks').innerHTML=P.checks.filter(x=>x.등급!=='OK').map(x=>
    '<tr><td><span class="pill '+(x.등급==='FAIL'?'fail':'warn')+'">'+(x.등급==='FAIL'?'어긋남':'확인')+'</span></td><td>'+esc(x.말)+'</td></tr>').join('')
    || '<tr><td colspan=2 class="note">어긋난 자리가 없습니다.</td></tr>';
  const gaps=P.items.filter(r=>!String(r['Purchase Order']||'').trim()||(!String(r['Material']||'').trim()&&!String(r['Vendor Part No.']||'').trim())).length;
  $('#sum').textContent='품목 '+P.items.length+'줄, 인보이스 '+P.inv.length+'장'
    +(gaps?', 손볼 줄 '+gaps:'' )+(P.llm.usd!=null?' | 모델 호출 '+P.llm.calls+'회, 약 '+Math.round(P.llm.usd*1400).toLocaleString()+'원':'');
  $('#legend').innerHTML='회색 열은 GERP 에서 채우는 열이라 비워 둡니다. 노란 줄은 PO 번호나 품목 코드가 없어 자동으로 못 잇는 줄입니다. '
    +'칸을 눌러 고치면 고친 값이 그대로 엑셀로 나갑니다. 줄을 누르면 오른쪽 원본에서 그 자리를 짚습니다.';
  $('#xlsx').disabled=false;
  drawPdf();
}

function select(i){
  document.querySelectorAll('#items tr').forEach(t=>t.classList.remove('sel'));
  const tr=$('#items').querySelector('tr[data-i="'+i+'"]'); if(tr) tr.classList.add('sel');
  document.querySelectorAll('.hl').forEach(e=>e.remove());
  (P.items[i]._hl||[]).forEach(([di,pn,x0,y0,x1,y1])=>{
    const wrap=document.getElementById('pg-'+di+'-'+pn); if(!wrap) return;
    const sz=P.sizes[di][pn-1], s=wrap.clientWidth/sz[0];
    const e=document.createElement('div'); e.className='hl';
    e.style.cssText='left:'+(x0*s-2)+'px;top:'+(y0*s-2)+'px;width:'+((x1-x0)*s+4)+'px;height:'+((y1-y0)*s+4)+'px';
    wrap.appendChild(e);
    wrap.scrollIntoView({block:'center',behavior:'smooth'});
  });
}

async function drawPdf(){
  if(docsRendered===P.case) return; docsRendered=P.case;
  const box=$('#pdf'); box.innerHTML='<p class="note">원본을 그리는 중입니다.</p>';
  let html='';
  const docs=[];
  for(const u of P.pdfs){ try{ docs.push(await pdfjsLib.getDocument(u).promise);}catch(e){docs.push(null);} }
  box.innerHTML='';
  for(let di=0;di<docs.length;di++){
    const d=docs[di]; if(!d) continue;
    for(let pn=1;pn<=d.numPages;pn++){
      const page=await d.getPage(pn), vp=page.getViewport({scale:1.6});
      const wrap=document.createElement('div'); wrap.className='pg'; wrap.id='pg-'+di+'-'+pn;
      const cv=document.createElement('canvas'); cv.width=vp.width; cv.height=vp.height;
      wrap.appendChild(cv); box.appendChild(wrap);
      await page.render({canvasContext:cv.getContext('2d'),viewport:vp}).promise;
    }
  }
}

$('#xlsx').onclick=()=>{
  fetch('/api/xlsx/'+JOB,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({items:P.items,head:P.head,inv:P.inv,review:P.review})})
   .then(r=>r.blob()).then(b=>{
     const a=document.createElement('a'); a.href=URL.createObjectURL(b);
     a.download='GERP-품목표-'+P.case+'.xlsx'; a.click();
   });
};
</script></body></html>"""


# ---------- 서버 ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200, extra=None):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", code)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(PAGE)
        if p == "/api/samples":
            return self._json({"samples": [{"name": s["name"], "note": s["note"]} for s in sample_list()]})
        if p.startswith("/api/job/"):
            job = JOBS.get(p.rsplit("/", 1)[-1])
            if not job: return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
            return self._json({k: job.get(k) for k in ("id", "status", "steps", "wall", "error", "payload")})
        if p.startswith("/pdf/"):
            _, _, jid, idx = p.split("/", 3)
            job = JOBS.get(jid)
            try: path = job["pdfs"][int(idx)]
            except Exception: return self.send_error(404)
            with open(path, "rb") as f:
                return self._send(f.read(), "application/pdf")
        return self.send_error(404)

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/extract-sample":
            name = (self.path.split("name=", 1)[-1] if "name=" in self.path else "")
            from urllib.parse import unquote
            name = unquote(name)
            s = next((x for x in sample_list() if x["name"] == name), None)
            if not s: return self._json({"error": "표본을 찾을 수 없습니다"}, 404)
            return self._start(name, [os.path.join(s["dir"], f) for f in s["pdfs"]])
        if p == "/api/extract":
            items = wuxi_paths.upload_files(self, "pdf")  # cgi 는 파이썬 3.13 에서 없어졌다
            jid = uuid.uuid4().hex
            d = os.path.join(WORK, jid); os.makedirs(d, exist_ok=True)
            pdfs = []
            for it in items:
                if not getattr(it, "filename", None): continue
                fn = re.sub(r"[^A-Za-z0-9._\-가-힣]", "_", os.path.basename(it.filename)) or "doc.pdf"
                path = os.path.join(d, fn)
                with open(path, "wb") as f: f.write(it.file.read())
                pdfs.append(path)
            if not pdfs: return self._json({"error": "PDF 파일이 없습니다"}, 400)
            return self._start("올린 서류", pdfs, jid=jid)
        if p.startswith("/api/xlsx/"):
            job = JOBS.get(p.rsplit("/", 1)[-1])
            if not job or not job.get("payload"): return self._json({"error": "결과가 없습니다"}, 404)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or "{}") if n else {}
            t = {"head": [body.get("head") or job["payload"]["head"]],
                 "items": body.get("items") or job["payload"]["items"],
                 "inv": body.get("inv") or job["payload"]["inv"],
                 "review": [body.get("review") or job["payload"]["review"]]}
            out = os.path.join(WORK, f"{job['id']}.xlsx")
            gerp_xlsx.write_tables(t, out)
            with open(out, "rb") as f:
                return self._send(f.read(),
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return self.send_error(404)

    def _start(self, name, pdfs, jid=None):
        jid = jid or uuid.uuid4().hex
        job = {"id": jid, "name": name, "pdfs": pdfs, "status": "running", "steps": [], "payload": None}
        JOBS[jid] = job
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return self._json({"job": jid})


def main(argv=None):
    ap = argparse.ArgumentParser(description="하이닉스 중국법인 데모 화면을 띄운다.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8766))
    a = ap.parse_args(argv)
    os.makedirs(WORK, exist_ok=True)
    got = [s["name"] for s in sample_list()]
    ThreadingHTTPServer.allow_reuse_address = True
    print(f"http://127.0.0.1:{a.port}  표본 {len(got)}건: {', '.join(got) or '없음'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
