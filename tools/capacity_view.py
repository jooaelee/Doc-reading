# -*- coding: utf-8 -*-
"""처리 용량 화면. 확인 화면 상단의 Capacity 버튼이 연다.

단계별 엔진과 건당 토큰(실측)을 한 표로 두고, 그 합계가 구독 요금제별 처리 건수로 이어지는 선을 그린다.
수치는 `work/out/capacity_stats.json`(배치 결과에서 뽑은 평균)에서 온다. 값은 저장소에 안 넣는다.

요금제 환산의 근거는 셋이다.
  1. 실측: Max 20x 계정 하나가 5시간 창 안에 1,185건을 처리했고 한도에 안 걸렸다. 그래서 하한이다.
  2. 요금제 비율: Max 5x 는 Pro 의 5배, Max 20x 는 Pro 의 20배(5시간 창 기준, Anthropic 안내).
  3. 근무 가정: 하루 5시간 창 2개, 월 22일. 주간 한도는 재지 못했다.
"""
import json

PLANS = [("Pro", 20, 1 / 20), ("Max 5x", 100, 5 / 20), ("Max 20x", 200, 1.0)]
DT_MONTHLY = {"order": 11984, "bl": 9256, "bl_manual_ratio": 0.87, "order_manual_ratio": 0.40}
WINDOWS_PER_DAY, WORKDAYS = 2, 22
SONNET_IN, SONNET_OUT = 2.0, 10.0          # USD / MTok


def build(stats):
    a, s = stats["AIR"], stats["SEA"]
    thr = stats["throughput"]
    n = a["cases"] + s["cases"]
    w = lambda k: (a[k] * a["cases"] + s[k] * s["cases"]) / n            # 항공과 해상 표본 비중으로 섞는다
    per_case_cli = w("cache_read") + w("cache_write") + w("doc_text_tok") + w("img_tok") + w("out_tok")
    per_case_api_in = w("doc_text_tok") + w("img_tok") + w("sys_tok") * w("calls")
    per_case_api_usd = (per_case_api_in * SONNET_IN + w("out_tok") * SONNET_OUT) / 1e6
    base = thr["max_cases_in_5h"]
    plans = []
    for name, usd, ratio in PLANS:
        win = base * ratio
        month = win * WINDOWS_PER_DAY * WORKDAYS
        plans.append({"name": name, "usd": usd, "window": round(win), "day": round(win * WINDOWS_PER_DAY), "month": round(month),
                      "cover_bl": round(month / DT_MONTHLY["bl"] * 100), "tokens_month": round(month * per_case_cli / 1e6, 1),
                      "measured": ratio == 1.0})
    api_month_usd = DT_MONTHLY["bl"] * per_case_api_usd
    tok = {"fixed": round(w("cache_read") + w("cache_write")), "doc": round(w("doc_text_tok") + w("img_tok")), "out": round(w("out_tok"))}
    stages = [
        {"name": "수신과 쪽 분류", "kind": "rule", "engine": "규칙 기반 (Python)", "target": "항공, 해상 전체", "tok": 0, "time": "0.1초"},
        {"name": "스캔 판독", "kind": "ocr", "engine": "OCR (Mac Vision, 서버 RapidOCR)", "target": f"항공 {a['scan_cases']}건, 해상 {s['scan_cases']}건", "tok": 0, "time": "쪽당 0.3초 (Mac), 0.8초 (서버)"},
        {"name": "AWB 좌표 판독", "kind": "rule", "engine": "고정 양식 좌표 파서", "target": f"항공 {a['form_hits']}건", "tok": 0, "time": "0초"},
        {"name": "서류별 추출", "kind": "llm", "engine": "Claude Sonnet 5 (CLI)", "target": f"건당 호출 항공 {a['calls']:.1f}회, 해상 {s['calls']:.1f}회", "tok": round(per_case_cli),
         "time": f"호출당 중앙값 {a['wall_by_kind'].get('CI', 0)}~{s['wall_by_kind'].get('AWB', 0)}초"},
        {"name": "후처리와 검증", "kind": "rule", "engine": "검증 규칙 25개", "target": "항공, 해상 전체", "tok": 0, "time": "0.1초"},
        {"name": "KEROL 등록 행 생성", "kind": "rule", "engine": "일괄 등록 엑셀 60개 열", "target": "항공, 해상 전체", "tok": 0, "time": "0초"},
    ]
    return {"stages": stages, "plans": plans, "per_case_cli": round(per_case_cli), "per_case_api_usd": round(per_case_api_usd, 4), "tok": tok,
            "api_month_usd": round(api_month_usd), "case_p50": {"AIR": a["case_wall_p50"], "SEA": s["case_wall_p50"]},
            "measured": thr, "dt": DT_MONTHLY, "windows": WINDOWS_PER_DAY, "workdays": WORKDAYS, "cases": n}


CSS = """
#v-cap{display:none;overflow:auto;padding:26px 32px 64px;background:var(--page)}#v-cap.on{display:block}
.cap-wrap{max-width:1240px;margin:0 auto}
.cap-sec{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}
.cap-sec h2{font-size:15px;margin:0 0 4px;color:var(--ink)}
.cap-lead{font-size:12.5px;color:var(--ink-2);margin:0 0 16px;line-height:1.6}
.flow6{display:grid;grid-template-columns:repeat(6,1fr);gap:22px}
.st{position:relative;border:1px solid var(--line);border-radius:10px;padding:12px 12px 10px;background:var(--surface);display:flex;flex-direction:column;gap:6px}
.st:not(:last-child)::after{content:"";position:absolute;right:-17px;top:50%;width:12px;height:12px;border-top:2px solid var(--ink-3);border-right:2px solid var(--ink-3);transform:translateY(-50%) rotate(45deg)}
.st .no{font-size:11px;color:var(--ink-3);font-weight:600}.st .nm{font-size:14px;font-weight:600;color:var(--ink)}
.st .eng{font-size:11.5px;font-weight:600;border-radius:5px;padding:2px 7px;align-self:flex-start}
.st.rule .eng{background:var(--sunk);color:var(--ink-2)}.st.ocr .eng{background:#fdf1e1;color:#8a5200}.st.llm .eng{background:var(--blue-soft);color:var(--blue)}
.st.llm{border:1.5px solid var(--blue-2);box-shadow:0 0 0 3px var(--blue-soft)}
.st dl{margin:6px 0 0;font-size:12px;border-top:1px solid var(--line);padding-top:6px}.st dt{color:var(--ink-3);font-size:11px;margin-top:5px}.st dt:first-child{margin-top:0}.st dd{margin:1px 0 0;color:var(--ink);line-height:1.45}
.st dd.zero{color:var(--ok)}.st dd.big{font-weight:700;color:var(--blue)}
.tk{display:grid;grid-template-columns:1fr 250px;gap:24px;align-items:center}
.tk-bar{display:flex;height:34px;border-radius:7px;overflow:hidden;background:var(--sunk)}
.tk-bar div{height:100%}.tk-legend{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:12px;font-size:12px;line-height:1.55;color:var(--ink-2)}
.tk-legend b{display:flex;align-items:center;gap:6px;color:var(--ink);font-size:12.5px}.tk-legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.tk-legend .n{font-family:var(--mono,monospace);font-weight:600}
.tk-side{border-left:1px solid var(--line);padding-left:22px}.tk-side .l{font-size:12px;color:var(--ink-3)}.tk-side .v{font-size:24px;font-weight:700;color:var(--ink);margin:2px 0 10px}
.tk-side .v.api{color:var(--blue);font-size:20px}
.pt{display:grid;grid-template-columns:130px 90px 1fr 250px;border-top:1px solid var(--line)}
.pt>div{padding:12px 10px;border-bottom:1px solid var(--line);font-size:12.5px;display:flex;flex-direction:column;justify-content:center}
.pt .hd{font-size:11.5px;color:var(--ink-3);font-weight:600;background:var(--sunk);padding-top:8px;padding-bottom:8px}
.pt .pn{font-size:14px;font-weight:600}.pt .pn em{font-style:normal;font-size:10.5px;color:#fff;background:var(--blue-2);border-radius:8px;padding:1px 6px;margin-left:5px;vertical-align:1px}
.pt .sub{font-size:11.5px;color:var(--ink-3);margin-top:2px}
.pt .trk{position:relative;height:26px;background:var(--sunk);border-radius:5px}
.pt .fill{position:absolute;left:0;top:0;bottom:0;border-radius:5px}
.pt .fv{position:absolute;top:4px;font-size:12.5px;font-weight:700;white-space:nowrap}
.pt .mk{position:absolute;top:-6px;bottom:-6px;border-left:2px dashed var(--ink);z-index:2}
.pt .hd.barh{position:relative}.pt .hd .mkl{position:absolute;top:7px;transform:translateX(-50%);white-space:nowrap;color:var(--ink);background:var(--sunk);padding:0 4px}
.pt .vd b{font-size:13px}.pt .vd.bad b{color:var(--bad)}.pt .vd.ok b{color:var(--ok)}
.cap-foot{font-size:12px;color:var(--ink-3);line-height:1.75;margin-top:14px}.cap-foot b{color:var(--ink-2)}
.arch{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px 20px 14px}
.arch h2{font-size:15px;margin:0 0 12px;display:flex;justify-content:space-between;align-items:baseline}.arch h2 a{font-size:12px;font-weight:500;color:var(--blue)}
.atabs{display:flex;gap:6px;margin:0 0 8px}.atabs button{font-size:12.5px;padding:5px 12px;border-radius:7px;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);cursor:pointer;font-weight:600}
.atabs button.on{background:var(--ink);border-color:var(--ink);color:#fff}
.alead{font-size:12.5px;color:var(--ink-2);margin:0 0 12px;line-height:1.6}
.ftbl{display:none;width:100%;border-collapse:collapse;font-size:12.5px}.ftbl.on{display:table}
.ftbl th{text-align:left;background:var(--sunk);color:var(--ink-2);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);font-size:12px}
.ftbl td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.5}.ftbl td.f{font-family:var(--mono,monospace);font-weight:600;white-space:nowrap}
.ftbl td.n{text-align:right;font-family:var(--mono,monospace);color:var(--ink-2)}.ftbl .lg{font-size:11px;border-radius:4px;padding:1px 6px;background:var(--sunk);font-weight:600;white-space:nowrap}
.ftbl .lg.py{background:#e6f0fb;color:#1f4e9a}.ftbl .lg.sw{background:#fdece4;color:#a4461c}.ftbl .lg.js{background:#fbf5d8;color:#7a6200}
.ftbl td.fn{font-family:var(--mono,monospace);font-size:11.5px;color:var(--ink-3)}.ftbl tr.grp td{background:var(--page);font-weight:700;color:var(--ink);font-size:12px;padding:6px 10px}
.arch iframe{width:100%;height:1260px;border:0;border-radius:8px;background:var(--page)}
@media (max-width:1100px){.flow6{grid-template-columns:repeat(3,1fr)}.st::after{display:none}.tk{grid-template-columns:1fr}.tk-side{border-left:0;padding-left:0}.pt{grid-template-columns:110px 80px 1fr}.pt>div:nth-child(4n){grid-column:1/-1}}
"""


def html(cap):
    st = cap["stages"]
    cards = "".join(
        f'<div class="st {x["kind"]}"><div class="no">{i + 1}단계</div><div class="nm">{x["name"]}</div><div class="eng">{x["engine"]}</div>'
        f'<dl><dt>적용</dt><dd>{x["target"]}</dd><dt>토큰</dt>'
        + (f'<dd class="big mono">{x["tok"]:,}</dd>' if x["tok"] else '<dd class="zero">사용 안 함</dd>')
        + f'<dt>소요</dt><dd>{x["time"]}</dd></dl></div>'
        for i, x in enumerate(st))
    t = cap["tok"]; tot = t["fixed"] + t["doc"] + t["out"]
    pct = lambda v: v / tot * 100
    tokbar = (f'<div class="tk-bar"><div style="width:{pct(t["fixed"])}%;background:#9fb3c8"></div>'
              f'<div style="width:{max(pct(t["doc"]), 1.2)}%;background:var(--blue-2)"></div><div style="width:{max(pct(t["out"]), 0.6)}%;background:var(--ok)"></div></div>')
    legend = (f'<div class="tk-legend">'
              f'<div><b><i style="background:#9fb3c8"></i>Claude Code 기본 프롬프트 <span class="n">{t["fixed"]:,}</span></b>CLI가 호출마다 자동으로 붙이는 시스템 프롬프트와 도구 정의입니다. 캐시로 처리되며, API를 직접 호출하면 포함되지 않습니다.</div>'
              f'<div><b><i style="background:var(--blue-2)"></i>서류 입력 <span class="n">{t["doc"]:,}</span></b>서류 텍스트와 스캔 이미지입니다. 이미지 1장은 약 1,600 토큰입니다.</div>'
              f'<div><b><i style="background:var(--ok)"></i>추출 결과 <span class="n">{t["out"]:,}</span></b>모델이 돌려주는 JSON입니다.</div></div>')
    dt = cap["dt"]; need = dt["bl"]
    top = max(max(p["month"] for p in cap["plans"]), need) * 1.08
    pos = lambda v: v / top * 100
    rows = (f'<div class="pt"><div class="hd">요금제</div><div class="hd">월 요금</div>'
            f'<div class="hd">월 처리 가능 건수 <span style="font-weight:500">(점선: DT 월 BL {need:,}건)</span></div><div class="hd">DT 월 BL 물량 대비</div>')
    for p in cap["plans"]:
        ratio = p["month"] / need
        col = "var(--bad)" if ratio < 1 else ("var(--ask)" if ratio < 2 else "var(--ok)")
        inside = pos(p["month"]) > 55
        fv = (f'<span class="fv mono" style="right:calc({100 - pos(p["month"])}% + 8px);color:#fff">{p["month"]:,}건</span>' if inside
              else f'<span class="fv mono" style="left:calc({pos(p["month"])}% + 8px)">{p["month"]:,}건</span>')
        vd = (f'<div class="vd bad"><b>부족 ({round(ratio * 100)}%)</b><span class="sub">같은 요금제 계정 {int(-(-need // p["month"]))}개 필요</span></div>' if ratio < 1
              else f'<div class="vd ok"><b>처리 가능 ({ratio:.1f}배)</b><span class="sub">여유 {p["month"] - need:,}건</span></div>')
        rows += (f'<div><span class="pn">{p["name"]}{"<em>실측</em>" if p["measured"] else ""}</span><span class="sub">5시간당 {p["window"]:,}건</span></div>'
                 f'<div class="mono">${p["usd"]}</div>'
                 f'<div><div class="trk"><div class="fill" style="width:{pos(p["month"])}%;background:{col}"></div>{fv}<div class="mk" style="left:{pos(need)}%"></div></div></div>{vd}')
    rows += (f'<div><span class="pn">API</span><span class="sub">사용량 과금</span></div><div class="mono">종량</div>'
             f'<div><span>사용 한도 없음</span></div>'
             f'<div class="vd ok"><b>월 ${cap["api_month_usd"]:,}</b><span class="sub">DT 월 BL {need:,}건 기준, 건당 ${cap["per_case_api_usd"]}</span></div></div>')
    thr = cap["measured"]
    body = f"""<div class="view" id="v-cap"><div class="cap-wrap">
<div class="cap-sec"><h2>건당 처리 흐름</h2><p class="cap-lead">전수 배치 {cap['cases']:,}건의 평균입니다. 건당 처리 시간 중앙값은 항공 {cap['case_p50']['AIR']}초, 해상 {cap['case_p50']['SEA']}초입니다. 모델을 호출하는 단계는 4단계 하나이며, 나머지 단계는 토큰을 사용하지 않습니다.</p>
<div class="flow6">{cards}</div></div>
<div class="cap-sec"><h2>건당 토큰 구성</h2><p class="cap-lead">4단계에서 건당 {tot:,} 토큰을 사용합니다. 대부분은 서류 내용과 관계없이 CLI가 붙이는 기본 프롬프트입니다.</p>
<div class="tk"><div>{tokbar}{legend}</div><div class="tk-side"><div class="l">CLI 기준 건당 토큰</div><div class="v mono">{tot:,}</div><div class="l">API 직접 호출 시 건당 비용</div><div class="v api mono">${cap['per_case_api_usd']} <span style="font-size:13px;color:var(--ink-3);font-weight:500">(약 {round(cap['per_case_api_usd'] * 1400)}원)</span></div></div></div></div>
<div class="cap-sec"><h2>요금제별 월 처리 가능 건수</h2><p class="cap-lead">Max 20x 계정으로 측정한 처리량(5시간 동안 {thr['max_cases_in_5h']:,}건)을 기준으로 계산했습니다. 구독 요금제의 사용 한도는 5시간 단위로 초기화되므로, 근무 시간 중 5시간 구간을 하루 {cap['windows']}회 사용하고 한 달에 {cap['workdays']}일(영업일) 가동한다고 가정했습니다.</p>
{rows}
<div class="cap-foot">
<b>1)</b> DT 내부 집계(2개월 월평균): 오더(SO) {dt['order']:,}건(수작업 {round(dt['order_manual_ratio'] * 100)}%), BL {dt['bl']:,}건(수작업 {round(dt['bl_manual_ratio'] * 100)}%, {round(dt['bl'] * dt['bl_manual_ratio']):,}건). 출처는 research/ai-strategy/24-업스테이지-협력-보고.md 2-3절입니다.<br>
<b>2)</b> Max 20x 계정 1개로 {thr['total_cases']:,}건을 {thr['span_h']}시간 동안 처리했으며, 처리량이 가장 많았던 5시간 구간은 {thr['max_cases_in_5h']:,}건이었습니다. 사용 한도에 도달하지 않았으므로 표의 수치는 최소치입니다.<br>
<b>3)</b> Max 5x는 Pro의 5배, Max 20x는 Pro의 20배 사용량을 기준으로 환산했습니다. 주간 한도는 이번 측정에서 도달하지 않아 반영하지 않았으며, Claude Code와 claude.ai는 같은 사용 한도를 공유합니다.<br>
<b>4)</b> API 환산 단가는 Sonnet 5 기준 100만 토큰당 입력 $2, 출력 $10이며, 시스템 프롬프트 캐시 읽기는 0.1배를 적용했습니다. 원화는 1달러 1,400원으로 계산했습니다.
</div></div>
<div class="arch" id="arch"><h2>아키텍처<a id="arch-open" href="arch.html" target="_blank">새 창에서 열기</a></h2>
<div class="atabs"><button class="on" data-f="arch.html">처리 구조</button><button data-f="arch_code.html">코드 구조</button><button data-f="arch_seq.html">추출 호출 순서</button><button data-f="files">파일 목록</button></div>
<p class="alead" id="arch-lead"></p>
<iframe id="arch-frame" src="arch.html" title="DocExtract 아키텍처" loading="lazy"></iframe>{files_table(cap)}</div>
</div></div>"""
    js = r"""
const ARCH_LEAD={'arch.html':'선적서류(BL, CI, PL)가 들어와 KEROL 등록 값이 되기까지의 흐름입니다. 뷰어 아래쪽 VIEWS에서 스캔 서류, AWB 고정 양식, 모델 호출, 검증과 등록 경로를 하나씩 골라 볼 수 있습니다.',
 'arch_code.html':'같은 흐름을 파일과 함수 이름으로 그렸습니다. 점선은 모델을 거치지 않는 경로입니다.',
 'arch_seq.html':'서류 한 묶음을 추출할 때 프로세스 사이에 오가는 호출을 시간 순서로 그렸습니다.',
 'files':'추출 파이프라인을 이루는 파일입니다. 줄 수는 화면을 만든 시점의 저장소 기준입니다.'};
function archTab(f){ document.querySelectorAll('.atabs button').forEach(b=>b.classList.toggle('on',b.dataset.f===f)); document.getElementById('arch-lead').textContent=ARCH_LEAD[f];
  const fr=document.getElementById('arch-frame'), tb=document.getElementById('arch-files'), op=document.getElementById('arch-open');
  if(f==='files'){ fr.style.display='none'; tb.classList.add('on'); op.style.visibility='hidden'; }
  else { tb.classList.remove('on'); fr.style.display='block'; op.style.visibility='visible'; op.href=f; if(!fr.src.endsWith(f)) fr.src=f; fr.style.height=(f==='arch_seq.html'?'1180px':'1260px'); } }
document.querySelectorAll('.atabs button').forEach(b=>b.onclick=()=>archTab(b.dataset.f)); archTab('arch.html');
"""
    return body, js


REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
FILES = [
    ("요청과 화면", [
        ("tools/ui_server.py", "Python", "로컬 HTTP 서버. 업로드를 받아 작업 스레드에서 추출을 실행하고 진행 단계를 돌려줍니다", "run_job, Handler.do_POST"),
        ("tools/confirm_ui.py", "Python", "확인 화면 HTML 생성. 원본 PDF 위 값 위치 계산(글자층 검색, OCR 좌표)", "build_case, highlights, ocr_highlights"),
        ("tools/diagnostics_view.py", "Python", "Diagnostics 탭. 검증 결과 통계와 정답 대조", "build, _truth"),
        ("tools/capacity_view.py", "Python", "Throughput 탭. 건당 토큰, 요금제별 처리량, 아키텍처", "build, html"),
    ]),
    ("추출 파이프라인", [
        ("tools/docpipe.py", "Python", "파이프라인 본체. 쪽 분류, OCR 경로, 서류별 묶기, CLI 호출, 병합, 해상 B/L 보정, 검증, KEROL 행 생성", "classify, route_scans, split_groups, call_split, merge_parts, sea_fix, verify, to_so_row"),
        ("tools/awb_forms.py", "Python", "고정 양식 AWB 좌표 파서. 모델 없이 칸 위치로 값을 읽습니다", "parse_lotte, parse_pns, parse_nx"),
        ("tools/extract_mcp.py", "Python", "extract 도구 하나만 가진 MCP 서버. 도구 인자를 JSON 파일로 남깁니다", "main"),
        ("tools/ocr_vision.swift", "Swift", "Mac Vision OCR 실행 파일 소스. 줄 단위 글자와 좌표를 JSON으로 출력합니다", "VNRecognizeTextRequest"),
    ]),
    ("측정과 운영", [
        ("tools/batch_run.py", "Python", "전수 배치 실행. 사용 한도에 걸리면 멈췄다가 이어서 실행, 규칙만 다시 거는 재검증", "main, reverify"),
        ("tools/truth_score.py", "Python", "정답 대조 채점. 항목별, 케이스별 일치율", "score"),
        ("tools/truth_prep.py", "Python", "정답 작성용 재료(본문 텍스트, 쪽 이미지, 초안) 생성", "main"),
        ("tools/so_upload.py", "Python", "추출 결과를 KEROL 일괄 등록 엑셀 행으로 모읍니다", "load_preds, differing"),
    ]),
]


def files_table(cap):
    import os
    rows = ""
    tot = 0
    for grp, items in FILES:
        rows += f'<tr class="grp"><td colspan="5">{grp}</td></tr>'
        for path, lang, role, fns in items:
            try:
                n = sum(1 for _ in open(os.path.join(REPO, path), encoding="utf-8"))
            except OSError:
                n = 0
            tot += n
            cls = {"Python": "py", "Swift": "sw"}.get(lang, "js")
            rows += (f'<tr><td class="f">{path}</td><td><span class="lg {cls}">{lang}</span></td><td class="n">{n:,}</td>'
                     f'<td>{role}</td><td class="fn">{fns}</td></tr>')
    try:
        n_tests = len([f for f in os.listdir(os.path.join(REPO, "tests")) if f.startswith("test_") and f.endswith(".py")])
    except OSError:
        n_tests = 0
    rows += (f'<tr class="grp"><td colspan="5">합계 {tot:,}줄. 테스트 파일 {n_tests}개는 합성 값만 씁니다. '
             f'외부 의존성은 PyMuPDF, onnxruntime(RapidOCR), Claude Code CLI입니다.</td></tr>')
    return (f'<table class="ftbl" id="arch-files"><thead><tr><th>파일</th><th>언어</th><th style="text-align:right">줄 수</th><th>역할</th><th>주요 함수</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return build(json.load(f))
    except (OSError, KeyError, ValueError):
        return None
