# -*- coding: utf-8 -*-
"""검증 결과 통계 화면. 확인 화면 상단의 Diagnostics 버튼이 연다.

확인 화면에 실린 케이스 전체(data)에서 상태, 규칙, 서류 종류, 송하인, AWB 양식별로 센다.
정답 폴더(work/truth*)가 있으면 표시된 항목의 추출값을 정답과 견줘
"추출이 틀려서 걸린 것"과 "서류끼리 달라서 걸린 것"을 가른다. 값과 케이스 번호는 화면에 안 낸다.
"""
import collections as C
import glob
import html as H
import json
import os
import re

import docpipe
import wuxi_paths

TRUTH_DIRS = [wuxi_paths.work(d) for d in ("truth", "truth_sea", "truth_blind_air", "truth_blind_sea")]

# 규칙이 어느 서류를 보는지. 둘을 견주는 규칙은 짝으로 적는다.
RULE_DOCS = {
    "gross_weight": "PL-AWB", "pkg_vs_pieces": "PL-AWB", "pl_vs_ci_qty": "PL-CI", "net_weight": "PL-CI",
    "declared_value": "CI-AWB", "incoterms_vs_freight": "CI-AWB",
    "ci_total": "CI", "ci_line": "CI", "lca_fx": "CI", "currency": "CI",
    "pl_line_gross": "PL", "pl_line_net": "PL", "pl_net_le_gross": "PL", "measurement_cbm": "PL", "package_unit": "PL",
    "chargeable": "AWB", "mawb_check_digit": "AWB", "mawb_format": "AWB", "awb_charges": "AWB", "agent_iata_code": "AWB",
    "agent_code_vs_account": "AWB", "hawb_vs_mawb": "AWB", "freight_term": "AWB",
}
CROSS = {"gross_weight", "pkg_vs_pieces", "pl_vs_ci_qty", "net_weight", "declared_value", "incoterms_vs_freight"}
FORMAT = {"package_unit", "mawb_format", "agent_iata_code", "agent_code_vs_account", "freight_term", "hawb_vs_mawb"}
DOC_COLS = [("AWB", "AWB, B/L"), ("CI", "CI"), ("PL", "PL"), ("PL-AWB", "PL과 운송장"), ("PL-CI", "PL과 CI"), ("CI-AWB", "CI와 운송장")]
CAUSES = [
    ("cross", "서류 간 기재 차이", "서로 다른 서류가 같은 항목을 다르게 적었습니다."),
    ("inner", "서류 내부 계산 차이", "한 서류 안에서 합계나 계산이 맞지 않습니다."),
    ("absent", "서류에 값 없음", "필수값이 선적서류 어디에도 적혀 있지 않습니다."),
    ("scan", "스캔 판독값", "이미지에서 읽은 값이라 글자층과 대조할 수 없습니다."),
    ("notfound", "원문에서 확인 안 됨", "추출값이 원문 글자층에 없습니다. 추출 오류 가능성이 가장 큰 유형입니다."),
    ("format", "코드, 형식", "단위 코드 변환이나 번호 형식이 기준과 다릅니다."),
]

# 실무자가 아니어도 읽히게 규칙마다 풀이를 둔다.
EXPLAIN = {
    "pl_vs_ci_qty": "CI는 청구 단위(kg, 개, 세트)로, PL은 포장 단위로 수량을 적는 경우가 많습니다. 여러 품목을 인보이스 한 줄로 묶어 청구한 경우도 여기에 걸립니다.",
    "gross_weight": "KEROL에는 운송장 총중량이 등록됩니다(두 값이 다른 232건 중 229건). 실제 서류 예: PL에는 '6 CTN ON 1 PLT, G/W 28KG(INCL. PLT WT)'가, 운송장에는 25 kg이 적혀 있습니다. PL과 CI의 총중량은 송하인이 적은 값이고, 운송장의 총중량은 포워더나 항공사가 실제로 다시 잰 값입니다. 팔레트와 포장재 무게가 더해지거나 화물을 나눠 보내면 두 값이 달라집니다. 5% 이내 차이는 확인, 그 이상은 오류로 표시합니다.",
    "pkg_vs_pieces": "KEROL에는 운송장 수량이 등록됩니다(두 값이 다른 93건 중 87건). 실제 서류 예: PL에는 '4 box, 1 PALLET'이 적혀 포장 수 4로 읽히고, 운송장 수량 칸에는 1이 적혀 있습니다. PL은 상자 수를, 운송장은 상자를 묶은 팔레트 수를 적는 경우가 많습니다. 상자 20개를 팔레트 2개에 실으면 PL에는 20, 운송장에는 2가 적힙니다. 같은 화물을 다른 단위로 센 것이라 대부분 오류가 아닙니다.",
    "measurement_cbm": "PL에 적힌 치수로 계산한 부피와 서류에 적힌 부피(CBM)가 다릅니다. 치수 단위(mm, cm) 혼동이나 일부 포장에만 치수를 적은 경우가 많습니다.",
    "pl_line_gross": "PL 품목별 총중량을 더한 값이 PL 총계와 다릅니다. 품목 줄에 낱개 중량을 적는 양식이 많아 흔히 생기며, KEROL에는 총계만 등록됩니다.",
    "pl_line_net": "PL 품목별 순중량을 더한 값이 PL 총계와 다릅니다. 원인은 총중량 합계와 같습니다.",
    "ci_total": "인보이스 품목 금액을 더한 값이 총액과 다릅니다. 할인, 운임, 보험료가 총액에 들어갔거나 인보이스 여러 장이 함께 온 경우입니다.",
    "ci_line": "품목 한 줄의 수량과 단가를 곱한 값이 금액과 다릅니다. 단가 단위(1,000개당 등)가 다르거나 반올림한 경우입니다.",
    "flight1": "편명이나 선명을 스캔 이미지에서 읽었습니다. 글자층이 없어 원문 대조를 못 했으므로 이미지에서 확인하십시오.",
    "incoterms_vs_freight": "인코텀즈로 보면 운임을 내는 쪽과 운송장의 운임 조건(PREPAID, COLLECT)이 통상 조합과 다릅니다. 계약에 따라 정상일 수 있습니다.",
    "package_unit": "포장 단위 낱말(예: SKID, BUNDLE)을 KEROL 코드로 바꾸지 못했습니다. 코드표에 추가하면 사라집니다.",
    "chargeable": "운송장의 청구 중량이 실중량이나 용적 중량과 맞지 않습니다.",
    "mawb_check_digit": "MAWB 번호 끝자리 검증 숫자가 계산과 다릅니다. 원문에도 같은 번호가 적혀 있으면 서류 오기입니다.",
    "net_weight": "PL과 CI의 순중량이 서로 다릅니다.",
    "declared_value": "운송장 신고가액과 인보이스 총액이 다릅니다.",
    "pl_net_le_gross": "PL 순중량이 총중량보다 큽니다.",
    "_missing": "통화, 인보이스 총액, 인코텀즈는 모두 인보이스에서 옵니다. 인보이스 없이 운송장과 PL만 온 건이거나, 반송, 수리, 무상 화물처럼 가격을 적지 않은 인보이스가 대부분입니다.",
}
FIELD_DOC = {**{k: "CI" for k in ("invoice_no", "incoterms", "currency", "customer_po", "item_desc", "ci_total", "ci_lines", "ci_gross", "ci_net")},
             **{k: "PL" for k in ("pl_packages", "pl_gross", "pl_net", "pl_lines", "dims_cm")}}


def rule_key(k):
    return "ci_line" if k.startswith("ci_line") else k


def status(c):
    if c["fail"] or c["missing_req"]:
        return "bad"
    return "ask" if c["warn"] or c.get("diff") else "ok"


def cause_of(g, k, m):
    k = rule_key(k); m = str(m)
    if m.startswith("텍스트에 없음"): return "scan"
    if m.startswith("원문에 없는 값"): return "notfound"
    if k in CROSS: return "cross"
    if k in FORMAT: return "format"
    return "inner"


def doc_of(k):
    k = rule_key(k)
    return RULE_DOCS.get(k) or FIELD_DOC.get(k, "AWB")


def rule_label(k):
    import confirm_ui
    if k == "ci_line":
        return "인보이스 품목 금액 불일치"
    return confirm_ui.RULE_LABELS.get(k) or f"{confirm_ui.LABELS.get(k, k)} 원문 대조"


def _truth(data):
    """정답이 있는 케이스에서 표시 항목의 추출값이 정답과 같은지 센다."""
    import confirm_ui
    by = {c["case"]: c for c in data}
    tr = {}
    for d in TRUTH_DIRS:
        p = os.path.join(d, "cases.txt")
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            parts = ln.rstrip("\n").split("\t")
            f = os.path.join(d, parts[0] + ".json")
            if len(parts) >= 2 and parts[1] in by and os.path.exists(f):
                tr[parts[1]] = json.load(open(f, encoding="utf-8"))
    if not tr:
        return None
    per = C.defaultdict(lambda: [0, 0])     # 규칙 -> [정확, 오류]
    miss = [0, 0]                            # [정답에도 없음, 정답엔 있음]
    for case, tv in tr.items():
        c = by[case]
        rows = {k: ok for k, ok, _ in docpipe.compare(c["ex"], tv)}
        for g, k, m, *_ in c["verify"]:
            if g == "OK":
                continue
            fs = ["ci_lines"] if k.startswith("ci_line") else confirm_ui.RULE_FIELDS.get(k, [k])
            fs = [f for f in fs if f in rows]
            if not fs:
                continue
            per[rule_key(k)][0 if all(rows[f] for f in fs) else 1] += 1
        for k in c["missing_req"]:
            miss[0 if tv.get(k) in (None, "", []) else 1] += 1
    ok = sum(v[0] for v in per.values()); bad = sum(v[1] for v in per.values())
    return {"cases": len(tr), "ok": ok, "bad": bad, "per": {k: v for k, v in per.items()}, "miss": miss}


def _sub_gross(data):
    """차이 크기와 선적서류 구성으로 가른다. 여러 인보이스, 미분류 스캔 쪽이 있으면 PL 값이 화물 일부만 담았을 수 있다."""
    out = C.Counter(); num = docpipe.num
    for c in data:
        if not any(v[1] == "gross_weight" and v[0] != "OK" for v in c["verify"]):
            continue
        e = c["ex"]; pl, awb, ci = num(e.get("pl_gross")), num(e.get("awb_gross")), num(e.get("ci_gross"))
        ship = pl if pl is not None else ci
        kinds = [k for doc in c.get("page_kinds") or [] for k in doc]
        if pl and ci and abs(pl - ci) > max(2, .05 * max(pl, ci)): out["CI와 PL끼리도 다름"] += 1; continue
        if not ship or awb is None: out["기타"] += 1; continue
        d = abs(awb - ship) / max(awb, ship)
        if d <= .05: out["5% 이내: 재계량 오차"] += 1
        elif sum(1 for k in kinds if k in ("SCAN", "OTHER")) >= 2: out["미분류 스캔 쪽이 있어 PL이 일부만 반영됐을 수 있음"] += 1
        elif sum(1 for k in kinds if k == "PL") >= 2: out["인보이스 여러 장이 한 운송장에 묶임"] += 1
        elif awb > ship: out["운송장이 더 무거움: 팔레트, 포장재 무게 포함 여부"] += 1
        else: out["운송장이 더 가벼움: 일부만 선적 또는 기재 차이"] += 1
    return out.most_common()


def _sub_pkg(data):
    out = C.Counter(); num = docpipe.num
    for c in data:
        if not any(v[1] == "pkg_vs_pieces" and v[0] != "OK" for v in c["verify"]):
            continue
        p, a = num(c["ex"].get("pl_packages")), num(c["ex"].get("awb_pieces"))
        if p is None or a is None: out["기타"] += 1
        elif a < p: out["운송장 수량이 적음 (상자를 팔레트로 묶음)"] += 1
        else: out["운송장 수량이 많음 (낱개 단위 기재 등)"] += 1
    return out.most_common()


def _sub_missing(data):
    out = C.Counter(); ci_keys = {"currency", "ci_total", "incoterms", "invoice_no"}
    for c in data:
        if not c["missing_req"]:
            continue
        kinds = {k for doc in c.get("page_kinds") or [] for k in doc}
        if set(c["missing_req"]) & ci_keys:
            out["인보이스 없이 도착" if "CI" not in kinds else "인보이스에 가격, 조건 표기 없음"] += 1
        else:
            out["운송장 칸 판독 실패"] += 1
    return out.most_common()


def _shipper(n):
    n = re.sub(r"\s+", " ", str(n or "").upper()).strip()
    n = re.sub(r"[ ,.]*(CO\.?,? ?LTD\.?|LIMITED|INC\.?|CORPORATION|CORP\.?)$", "", n)
    return n[:30] or "(미상)"


DOC_ROOTS = {"AIR": wuxi_paths.work("docs_raw"), "SEA": wuxi_paths.work("docs_raw_sea")}
INCO_EQ = {("CIF", "CIP"), ("FOB", "FCA"), ("CFR", "CPT")}


def _kerol(data):
    """케이스 폴더의 meta.json(KEROL 목록 조회 행)에 담긴 담당자 등록값과 추출값을 견준다."""
    num = docpipe.num
    an = lambda x: re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
    fl = lambda x: re.sub(r"^([A-Z0-9]{2})0+(?=\d)", r"\1", an(x))

    def same_num(a, b):
        a, b = num(a), num(b)
        return abs(a - b) <= max(.01, .005 * max(abs(a), abs(b)))

    FIELDS = [("HBL No", "BL_NO", "hawb", lambda k, e: an(k) in an(e)),
              ("MBL No", "MBL_NO", "mawb", lambda k, e: an(k)[-8:] == an(e)[-8:]),
              ("Shipper", "SHIPPER_NM", "shipper_name", lambda k, e: an(k)[:8] in an(e) or an(e)[:8] in an(k)),
              ("Gross Weight", "GROSS_WEIGHT", "awb_gross", same_num),
              ("Package Qty.", "PACKAGE_QTY", "awb_pieces", same_num),
              ("Chargeable Weight", "CHARGEABLE_WEIGHT", "awb_chargeable", same_num),
              ("Incoterms", "INCOTERMS_NM", "incoterms", lambda k, e: an(k)[:3] == an(e)[:3]),
              ("Invoice Cur", "CURR_CD", "currency", lambda k, e: an(k) == an(e)),
              ("Flight / Vessel", "FLIGHT1_NO|VESSEL_NM", "flight1", lambda k, e: fl(k)[:6] in fl(e))]
    out = {m: C.OrderedDict((f[0], C.Counter()) for f in FIELDS) for m in ("AIR", "SEA")}
    src = {"pkg": C.Counter(), "gw": C.Counter()}
    n = 0
    for c in data:
        f = os.path.join(DOC_ROOTS.get(c["mode"], ""), c["case"], "meta.json")
        if not os.path.exists(f):
            other = DOC_ROOTS["SEA" if c["mode"] == "AIR" else "AIR"]
            f = os.path.join(other, c["case"], "meta.json")
            if not os.path.exists(f):
                continue
        m = json.load(open(f, encoding="utf-8")); e = c["ex"]; n += 1
        mode = "SEA" if "VESSEL_NM" in m else "AIR"
        for label, kk, ek, same in FIELDS:
            kv = m.get(kk.split("|")[1] if mode == "SEA" and "|" in kk else kk.split("|")[0])
            ev = e.get(ek)
            if kv in (None, "", 0, "0", "0.0"):
                continue
            if ev in (None, "", []):
                out[mode][label]["추출 없음"] += 1
            elif same(kv, ev):
                out[mode][label]["일치"] += 1
            elif label == "Incoterms" and (an(kv)[:3], an(ev)[:3]) in INCO_EQ:
                out[mode][label]["표기 차이"] += 1
            else:
                out[mode][label]["다름"] += 1
        kq, aq, pq = num(m.get("PACKAGE_QTY")), num(e.get("awb_pieces")), num(e.get("pl_packages"))
        if kq and aq and pq and aq != pq:
            src["pkg"]["운송장" if kq == aq else "PL" if kq == pq else "기타"] += 1
        kg, ag, pg = num(m.get("GROSS_WEIGHT")), num(e.get("awb_gross")), num(e.get("pl_gross"))
        if kg and ag and pg and not same_num(ag, pg):
            src["gw"]["운송장" if same_num(kg, ag) else "PL" if same_num(kg, pg) else "기타"] += 1
    if not n:
        return None
    return {"cases": n, "fields": {m: {k: dict(v) for k, v in d.items() if sum(v.values())} for m, d in out.items()}, "src": {k: dict(v) for k, v in src.items()}}


PROMO_RULES = [
    {"id": "gross_weight", "title": "총중량은 운송장 값을 등록한다",
     "why": "PL 중량은 송하인이 적은 값이고, 운송장 중량은 운송사가 다시 잰 값입니다. 팔레트 무게나 계량 오차로 달라지며, 담당자는 운송장 값을 등록합니다.",
     "src": "gw"},
    {"id": "pkg_vs_pieces", "title": "포장 수는 운송장 수량을 등록한다",
     "why": "PL은 상자 수를, 운송장은 상자를 실은 팔레트 수를 적습니다. 상자 20개를 팔레트 2개에 실으면 PL 20, 운송장 2가 되며, 담당자는 운송장 수량을 등록합니다.",
     "src": "pkg"},
    {"id": "shipper_history", "title": "서류에 통화, 인코텀즈가 없으면 같은 송하인의 등록값을 쓴다",
     "why": "인보이스가 없거나 가격 표기가 없는 건도 담당자는 통화와 인코텀즈를 채워 등록합니다. 같은 송하인의 이전 KEROL 등록값을 쓰면 대부분 담당자 입력과 같습니다.",
     "src": "hist"},
]


def _promotable(c):
    """승격 규칙이 덮는 검증 항목. 운송장 값이 있고, 송하인 서류끼리(CI, PL)는 맞을 때만 덮는다."""
    num = docpipe.num; e = c["ex"]; out = []
    pl, ci, awb = num(e.get("pl_gross")), num(e.get("ci_gross")), num(e.get("awb_gross"))
    if awb is not None and not (pl and ci and abs(pl - ci) > max(2, .05 * max(pl, ci))):
        out.append("gross_weight")
    if num(e.get("awb_pieces")) is not None:
        out.append("pkg_vs_pieces")
    return out


def _status_with(c, promoted):
    f = w = 0
    missing = [k for k in c["missing_req"] if not ("shipper_history" in promoted and k in c.get("fill", {}))]
    for g, k, *_ in c["verify"]:
        if g == "OK" or (k in promoted and k in c.get("promo", [])):
            continue
        if g == "FAIL": f += 1
        else: w += 1
    if f or missing: return "bad"
    return "ask" if w or c.get("diff") else "ok"


def _history_fill(data):
    """필수값 중 통화, 인코텀즈가 빈 건에 같은 송하인의 KEROL 등록 이력(자기 건 제외) 최빈값을 채운다. 맞힌 수도 센다."""
    an = lambda x: re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
    metas = {}
    for c in data:
        for root in DOC_ROOTS.values():
            f = os.path.join(root, c["case"], "meta.json")
            if os.path.exists(f):
                metas[c["case"]] = json.load(open(f, encoding="utf-8")); break
    hist = C.defaultdict(lambda: C.defaultdict(C.Counter))
    for m in metas.values():
        for f in ("CURR_CD", "INCOTERMS_NM"):
            if m.get(f): hist[an(m.get("SHIPPER_NM"))[:10]][f][an(m.get(f))[:3]] += 1
    score = C.Counter()
    for c in data:
        c["fill"] = {}
        m = metas.get(c["case"])
        if not m:
            continue
        for k, f in (("currency", "CURR_CD"), ("incoterms", "INCOTERMS_NM")):
            if k not in c["missing_req"]:
                continue
            h = hist[an(m.get("SHIPPER_NM"))[:10]][f].copy()
            own = an(m.get(f))[:3]
            if own: h[own] -= 1
            h = +h
            if not h:
                continue
            top = h.most_common(1)[0][0]
            c["fill"][k] = top
            if own:
                score["hit" if top == own else "miss"] += 1
    return {"운송장": score["hit"], "기타": score["miss"]}


def _promotion(data, kerol):
    hist_src = _history_fill(data)
    for c in data:
        c["promo"] = [k for k in _promotable(c) if any(v[1] == k and v[0] != "OK" for v in c["verify"])]
    rules = []
    for r in PROMO_RULES:
        src = hist_src if r["src"] == "hist" else (kerol or {}).get("src", {}).get(r["src"], {})
        n = sum(src.values()); hit = src.get("운송장", 0)
        covered = sum(1 for c in data if (c["fill"] if r["id"] == "shipper_history" else r["id"] in c["promo"]))
        after = C.Counter(_status_with(c, {r["id"]}) for c in data)
        rules.append({**r, "evidence_n": n, "evidence_hit": hit, "covered": covered, "after": dict(after)})
    both = C.Counter(_status_with(c, {r["id"] for r in PROMO_RULES}) for c in data)
    return {"rules": rules, "after_all": dict(both)}


def build(data):
    kerol = _kerol(data)
    st = C.Counter(status(c) for c in data)
    by_mode = {m: C.Counter(status(c) for c in data if c["mode"] == m) for m in ("AIR", "SEA")}
    rules = C.defaultdict(lambda: {"FAIL": 0, "WARN": 0, "AIR": 0, "SEA": 0, "cases": set()})
    matrix = C.Counter()
    for c in data:
        for g, k, m, *_ in c["verify"]:
            if g == "OK":
                continue
            r = rules[rule_key(k)]
            r[g] += 1; r[c["mode"]] += 1; r["cases"].add(c["case"])
            matrix[(cause_of(g, k, m), doc_of(k))] += 1
        for k in c["missing_req"]:
            matrix[("absent", FIELD_DOC.get(k, "AWB"))] += 1
    n_missing = sum(1 for c in data if c["missing_req"])
    rule_rows = sorted(({"key": k, "label": rule_label(k), "fail": v["FAIL"], "warn": v["WARN"], "air": v["AIR"], "sea": v["SEA"],
                         "cases": len(v["cases"]), "doc": doc_of(k), "cause": cause_of("WARN", k, ""), "explain": EXPLAIN.get(k, "")}
                        for k, v in rules.items()), key=lambda x: -x["cases"])
    ship = C.defaultdict(C.Counter); ship_reason = C.defaultdict(C.Counter)
    for c in data:
        n = _shipper(c["ex"].get("shipper_name"))
        ship[n][status(c)] += 1
        if status(c) == "bad":
            for k in c["missing_req"]:
                ship_reason[n]["필수값 누락"] += 1
            for g, k, m, *_ in c["verify"]:
                if g == "FAIL":
                    ship_reason[n][rule_label(rule_key(k))] += 1
    ships = sorted(ship.items(), key=lambda x: -sum(x[1].values()))[:12]
    forms = C.defaultdict(C.Counter)
    for c in data:
        forms[c.get("form") or ("해상 B/L" if c["mode"] == "SEA" else "항공 기타 양식")][status(c)] += 1
    return {
        "total": len(data), "status": dict(st), "by_mode": {m: dict(v) for m, v in by_mode.items()},
        "n_missing": n_missing, "n_fail_rule": sum(1 for c in data if c["fail"]),
        "rules": rule_rows, "matrix": {f"{a}|{b}": n for (a, b), n in matrix.items()},
        "sub": {"gross_weight": _sub_gross(data), "pkg_vs_pieces": _sub_pkg(data), "_missing": _sub_missing(data)},
        "ships": [{"name": n, **dict(v), "n": sum(v.values()), "reason": ship_reason[n].most_common(1)[0][0] if ship_reason[n] else ""} for n, v in ships],
        "forms": sorted(({"name": n, **dict(v), "n": sum(v.values())} for n, v in forms.items()), key=lambda x: -x["n"]),
        "truth": _truth(data), "kerol": kerol, "promotion": _promotion(data, kerol),
    }


SHORT = {
    "pl_vs_ci_qty": "CI는 청구 단위, PL은 포장 단위로 셈", "gross_weight": "송하인 기재 중량과 운송사 실측 중량의 차이",
    "measurement_cbm": "치수로 계산한 부피와 기재 부피의 차이", "pkg_vs_pieces": "PL은 상자 수, 운송장은 팔레트 수",
    "_missing": "인보이스가 없거나 가격 표기가 없음", "pl_line_gross": "품목 줄에 낱개 중량을 기재", "pl_line_net": "품목 줄에 낱개 중량을 기재",
    "ci_total": "할인, 운임 포함 또는 인보이스 여러 장", "flight1": "스캔 이미지에서 읽은 값", "ci_line": "단가 단위나 반올림 차이",
    "incoterms_vs_freight": "거래 조건과 운임 조건 조합 확인", "package_unit": "포장 단위 코드 미등록", "chargeable": "청구 중량 계산 확인",
    "mawb_check_digit": "번호 끝자리 검증 불일치", "net_weight": "PL과 CI 순중량 차이", "declared_value": "신고가액과 인보이스 총액 차이",
}
CAUSE_COLOR = {"cross": "#5b7fb5", "inner": "#8a7bb5", "absent": "#b57d94", "scan": "#bf9a58", "notfound": "#c46f64", "format": "#5f9a92"}
DOC_COLOR = {"AWB": "#6d8fb3", "CI": "#8a7bb5", "PL": "#5f9a92", "PL-AWB": "#5b7fb5", "PL-CI": "#7d8fa6", "CI-AWB": "#a08a6a"}


CSS = """
#v-diag{display:none;overflow:auto;padding:24px 32px 64px;background:var(--page)}#v-diag.on{display:block}
#v-diag{--d-bad:#c46f64;--d-ask:#d4a24c;--d-ok:#5f9e7c;--d-bar:#8ea3b9;--d-soft:#f1f4f8}
.dg{max-width:1240px;margin:0 auto}
.dg-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin:0 0 14px}
.dg-head h1{font-size:19px;margin:0;color:var(--ink)}.dg-head p{margin:4px 0 0;font-size:13.5px;color:var(--ink-2);line-height:1.6}.dg-head p b{color:var(--ink)}
.dg-kpi{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px}
.kp{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;cursor:pointer;position:relative;overflow:hidden}
.kp::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--d-bar)}
.kp.ok::before{background:var(--d-ok)}.kp.ask::before{background:var(--d-ask)}.kp.bad::before{background:var(--d-bad)}.kp.acck::before{background:#3d6fa3}
.kp:hover{border-color:#b9c6d6}
.kp .l{font-size:12px;color:var(--ink-3)}.kp .v{font-size:26px;font-weight:700;color:var(--ink);line-height:1.25;margin-top:2px}.kp .v small{font-size:13px;color:var(--ink-3);font-weight:500;margin-left:4px}
.kp .s{font-size:11.5px;color:var(--ink-3);margin-top:2px}
.modebar{display:grid;grid-template-columns:44px 1fr;gap:6px 10px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:var(--ink-2)}
.sb{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--d-soft)}.sb i{display:block;height:100%}
.sb .bad{background:var(--d-bad)}.sb .ask{background:var(--d-ask)}.sb .ok{background:var(--d-ok)}
.lg{display:flex;gap:14px;font-size:11.5px;color:var(--ink-3)}.lg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px}
.dg-grid{display:grid;grid-template-columns:1.5fr 1fr 1fr;gap:12px;margin-bottom:16px}
.pn{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.pn h3{font-size:13.5px;margin:0 0 2px;color:var(--ink)}.pn .hs{font-size:11.5px;color:var(--ink-3);margin:0 0 12px}
.hb{display:grid;grid-template-columns:1fr 44px;gap:2px 8px;align-items:center;margin-bottom:9px;cursor:pointer}
.hb .t{font-size:12.5px;color:var(--ink);display:flex;justify-content:space-between;gap:10px;min-width:0}.hb .t span{flex:none}.hb .t em{min-width:0}.hb .t em{font-style:normal;color:var(--ink-3);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hb .b{grid-column:1;height:7px;border-radius:4px;background:var(--d-soft);overflow:hidden}.hb .b i{display:block;height:100%;border-radius:4px}
.hb .n{grid-row:1/3;grid-column:2;text-align:right;font-size:13px;font-weight:600;color:var(--ink-2)}
.hb:hover .t{color:#3d6fa3}
.pn .note{font-size:11.5px;color:var(--ink-3);margin-top:8px;line-height:1.55;border-top:1px dashed var(--line);padding-top:8px}
.accsec{display:grid;grid-template-columns:200px 1fr;gap:22px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.accsec .big{font-size:40px;font-weight:700;color:#3d6fa3;line-height:1}.accsec .big small{font-size:16px}
.accsec .cap{font-size:12px;color:var(--ink-2);margin-top:6px;line-height:1.55}
.accsec h3{font-size:13.5px;margin:0 0 4px}.accsec .hs{font-size:11.5px;color:var(--ink-3);margin:0 0 10px;line-height:1.55}
.ab{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 22px}
.ab div{display:grid;grid-template-columns:150px 1fr 44px;gap:8px;align-items:center;font-size:12px}
.ab .sb{height:8px}.ab .sb .ok{background:#7fa9cf}.ab .sb .bad{background:var(--d-bad)}.ab span.m{color:var(--ink-3);text-align:right;font-family:var(--mono,monospace)}
.kr{display:grid;grid-template-columns:250px 1fr;gap:24px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.kr .big{font-size:40px;font-weight:700;color:#3d6fa3;line-height:1}.kr .big small{font-size:16px}.kr .cap{font-size:12px;color:var(--ink-2);margin-top:6px;line-height:1.55}
.kr-call{margin-top:12px;background:#eef4fa;border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.6;color:var(--ink-2)}.kr-call b{display:block;color:var(--ink);font-size:12.5px;margin-bottom:2px}
.kr h3{font-size:13.5px;margin:0 0 4px}.kr .hs{font-size:11.5px;color:var(--ink-3);margin:0 0 10px;line-height:1.6}.kr .kl i{display:inline-block;width:8px;height:8px;border-radius:2px;margin:0 3px 0 8px}
.kr-g{display:grid;grid-template-columns:1fr 1fr;gap:6px 26px}.kr-g h4{font-size:12px;margin:0 0 6px;color:var(--ink-2)}
.kr-g>div>div{display:grid;grid-template-columns:120px 1fr 40px;gap:8px;align-items:center;font-size:12px;margin-bottom:5px}.kr-g .sb{height:8px}.kr-g span.m{text-align:right;color:var(--ink-3);font-family:var(--mono,monospace)}
.pr{display:grid;grid-template-columns:1fr 300px;gap:22px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:12px}
.pr h3{font-size:14px;margin:0 0 4px}.pr .hs{font-size:12px;color:var(--ink-3);margin:0 0 10px;line-height:1.6}
.pr-r{display:grid;grid-template-columns:1fr 150px;gap:14px;align-items:center;border-top:1px solid var(--line);padding:10px 8px}
.pr-r b{font-size:13px}.pr-r p{margin:3px 0 4px;font-size:12px;color:var(--ink-2);line-height:1.6}.pr-r .ev{font-size:11.5px;color:#3d6fa3;font-weight:600}
.pr-b{border:1px solid #3d6fa3;background:#3d6fa3;color:#fff;border-radius:7px;padding:8px 10px;font-size:12.5px;font-weight:600;cursor:pointer}
.pr-r.on .pr-b{background:#fff;color:#3d6fa3}.pr-r.on{background:linear-gradient(90deg,#eef4fa,transparent)}
.pr-s{border-left:1px solid var(--line);padding-left:20px;display:flex;flex-direction:column;justify-content:center}
.pr-t{font-size:12px;color:var(--ink-3);font-weight:600;margin-bottom:8px}.pr-g{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.pr-c{background:var(--d-soft);border-radius:8px;padding:9px 10px}.pr-c span{display:block;font-size:11.5px;color:var(--ink-3)}.pr-c b{font-size:24px}
.pr-c.bad b{color:var(--d-bad)}.pr-c.ask b{color:#b58420}.pr-c.ok b{color:var(--d-ok)}.pr-c em{display:block;font-style:normal;font-size:11.5px;font-weight:600;min-height:16px}
.pr-c em.dn{color:var(--d-ok)}.pr-c em.up{color:var(--d-bad)}.pr-c.ok em.up{color:var(--d-ok)}.pr-c.ok em.dn{color:var(--d-bad)}
.pr-n{font-size:11.5px;color:var(--ink-3);margin-top:8px}
.sec-t{font-size:14px;font-weight:700;margin:22px 0 8px;color:var(--ink)}.sec-t small{font-weight:400;color:var(--ink-3);font-size:12px;margin-left:8px}
.rl{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.rl details{border-bottom:1px solid var(--line)}.rl details:last-child{border-bottom:0}
.rl summary{list-style:none;display:grid;grid-template-columns:18px 230px 110px 70px 1fr 90px;gap:10px;align-items:center;padding:10px 14px;cursor:pointer;font-size:12.5px}
.rl summary::-webkit-details-marker{display:none}.rl summary .ar{color:var(--ink-3);transition:transform .15s}.rl details[open] summary .ar{transform:rotate(90deg)}
.rl summary:hover{background:var(--d-soft)}.rl summary b{font-size:13px}.rl .cnt{font-weight:700;text-align:right}.rl .sh{color:var(--ink-2)}
.chip{font-size:10.5px;border-radius:4px;padding:1px 6px;font-weight:600;white-space:nowrap;justify-self:start}
.rl .go{font-size:12px;color:#3d6fa3;font-weight:600;text-align:right}.rl .go:hover{text-decoration:underline}
.rl .body{padding:2px 14px 14px 42px;display:grid;grid-template-columns:1fr 240px;gap:24px;font-size:12.5px;color:var(--ink-2);line-height:1.7}
.rl .sub{margin-top:10px}.rl .sub div{display:grid;grid-template-columns:260px 1fr 34px;gap:8px;align-items:center;font-size:12px;margin-top:4px}
.rl .sub .sb{height:7px}.rl .sub .sb i{background:#7fa9cf}
.rl .tv{background:var(--d-soft);border-radius:8px;padding:10px 12px;font-size:12px;align-self:start}.rl .tv b{font-size:15px;color:var(--ink)}
.sv{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.shr{display:grid;grid-template-columns:190px 1fr 36px;gap:4px 10px;align-items:center;font-size:12px;margin-bottom:7px}
.shr .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink)}.shr .n{text-align:right;color:var(--ink-3)}
.shr .rs{grid-column:2/4;font-size:11px;color:var(--d-bad);margin-top:-3px}
@media (max-width:1100px){.dg-kpi{grid-template-columns:repeat(3,1fr)}.dg-grid,.sv,.accsec{grid-template-columns:1fr}.rl summary{grid-template-columns:18px 1fr 70px}.rl summary .chip,.rl summary .sh,.rl summary .go{display:none}.rl .body{grid-template-columns:1fr}.ab{grid-template-columns:1fr}}
"""


def _pct(a, b):
    return round(a / b * 100) if b else 0


def html(dg):
    t = dg["total"]; s = dg["status"]
    bad, ask, ok = s.get("bad", 0), s.get("ask", 0), s.get("ok", 0)
    tr = dg["truth"]
    names = dict(DOC_COLS); causes = {k: n for k, n, _ in CAUSES}
    rows_all = [{"key": "_missing", "label": "필수값 누락", "fail": dg["n_missing"], "warn": 0, "cases": dg["n_missing"], "doc": "CI", "cause": "absent",
                 "explain": EXPLAIN["_missing"]}] + dg["rules"]
    rows_all.sort(key=lambda x: -x["cases"])
    acc = _pct(tr["ok"], tr["ok"] + tr["bad"]) if tr and tr["ok"] + tr["bad"] else None

    cause_tot = C.Counter(); doc_tot = C.Counter()
    for k, n in dg["matrix"].items():
        a, b = k.split("|"); cause_tot[a] += n; doc_tot[b] += n
    flags = sum(cause_tot.values()) or 1
    cross_share = _pct(cause_tot["cross"] + cause_tot["inner"], flags)

    head = (f'<div class="dg-head"><div><h1>검증 결과</h1><p>표시된 항목의 <b>{cross_share}%</b>는 서류끼리, 또는 한 서류 안에서 적힌 값이 서로 달라 생겼습니다.'
            + (f' 정답과 대조하면 표시 항목의 <b>{acc}%</b>는 추출값이 정확했습니다.' if acc is not None else "")
            + '</p></div><div class="lg"><span><i style="background:var(--d-bad)"></i>오류</span><span><i style="background:var(--d-ask)"></i>확인</span><span><i style="background:var(--d-ok)"></i>정상</span></div></div>')
    kpi = (f'<div class="dg-kpi">'
           f'<div class="kp" onclick="diagGo(\'all\')"><div class="l">전체</div><div class="v mono">{t:,}<small>건</small></div><div class="s">항공 {sum(dg["by_mode"]["AIR"].values())}, 해상 {sum(dg["by_mode"]["SEA"].values())}</div></div>'
           f'<div class="kp ok" onclick="diagGo(\'ok\')"><div class="l">정상</div><div class="v mono" id="d-ok">{ok:,}<small>{_pct(ok, t)}%</small></div><div class="s">모든 규칙 통과</div></div>'
           f'<div class="kp ask" onclick="diagGo(\'ask\')"><div class="l">확인</div><div class="v mono" id="d-ask">{ask:,}<small>{_pct(ask, t)}%</small></div><div class="s">경고만 있음</div></div>'
           f'<div class="kp bad" onclick="diagGo(\'bad\')"><div class="l">오류</div><div class="v mono" id="d-bad">{bad:,}<small>{_pct(bad, t)}%</small></div><div class="s">필수값 누락 {dg["n_missing"]}, 규칙 위반 {dg["n_fail_rule"]}</div></div>'
           + (f'<div class="kp acck"><div class="l">표시 항목 추출 정확도</div><div class="v mono">{acc}<small>%</small></div><div class="s">정답 {tr["cases"]}건 대조, 표시 {tr["ok"] + tr["bad"]}개 기준</div></div>' if acc is not None else "")
           + '</div>')
    mb = '<div class="modebar">'
    for m, name in (("AIR", "항공"), ("SEA", "해상")):
        v = dg["by_mode"][m]; n = sum(v.values()) or 1
        mb += f'<span>{name}</span><div class="sb">' + "".join(f'<i class="{k}" style="width:{v.get(k, 0) / n * 100}%" title="{v.get(k, 0)}건"></i>' for k in ("bad", "ask", "ok")) + "</div>"
    mb += "</div>"

    top = rows_all[:7]; mx = max(r["cases"] for r in top)
    p1 = '<div class="pn"><h3>많이 걸린 검증 항목</h3><p class="hs">건수 기준 상위 7개. 누르면 해당 건만 검토 목록에 보입니다.</p>' + "".join(
        f'<div class="hb" onclick="diagGo(\'rule:{r["key"]}\')"><div class="t"><span>{H.escape(str(r["label"]))}</span><em>{SHORT.get(r["key"], "")}</em></div>'
        f'<div class="b"><i style="width:{r["cases"] / mx * 100}%;background:{"var(--d-bad)" if r["fail"] >= r["warn"] else "var(--d-ask)"}"></i></div><span class="n mono">{r["cases"]}</span></div>'
        for r in top) + "</div>"
    mxc = max(cause_tot.values() or [1])
    p2 = '<div class="pn"><h3>원인 유형</h3><p class="hs">표시 항목 ' + f'{flags:,}개를 원인별로 나눴습니다.</p>' + "".join(
        f'<div class="hb" style="cursor:default"><div class="t"><span>{n}</span><em>{_pct(cause_tot[k], flags)}%</em></div>'
        f'<div class="b"><i style="width:{cause_tot[k] / mxc * 100}%;background:{CAUSE_COLOR[k]}"></i></div><span class="n mono">{cause_tot[k]}</span></div>'
        for k, n, _ in sorted(CAUSES, key=lambda x: -cause_tot[x[0]])) + (
        f'<div class="note">추출 오류 가능성이 가장 큰 "원문에서 확인 안 됨"은 {cause_tot["notfound"]}개({cause_tot["notfound"] / flags * 100:.1f}%)입니다.</div>') + "</div>"
    mxd = max(doc_tot.values() or [1])
    p3 = '<div class="pn"><h3>관련 서류</h3><p class="hs">어느 서류를 보는 규칙에서 걸렸는지 나눴습니다.</p>' + "".join(
        f'<div class="hb" style="cursor:default"><div class="t"><span>{n}</span><em>{_pct(doc_tot[k], flags)}%</em></div>'
        f'<div class="b"><i style="width:{doc_tot[k] / mxd * 100}%;background:{DOC_COLOR[k]}"></i></div><span class="n mono">{doc_tot[k]}</span></div>'
        for k, n in sorted(DOC_COLS, key=lambda x: -doc_tot[x[0]])) + '<div class="note">두 서류를 견주는 규칙은 짝으로 셌습니다. PL과 운송장 사이 차이가 가장 많습니다.</div></div>'

    acc_html = ""
    if acc is not None:
        items = ""
        for r in dg["rules"]:
            v = tr["per"].get(r["key"])
            if not v or sum(v) < 3:
                continue
            n = sum(v)
            items += (f'<div><span>{H.escape(str(r["label"]))}</span><div class="sb"><i class="ok" style="width:{v[0] / n * 100}%"></i><i class="bad" style="width:{v[1] / n * 100}%"></i></div>'
                      f'<span class="m">{v[0]}/{n}</span></div>')
        m0, m1 = tr["miss"]
        acc_html = (f'<div class="accsec"><div><div class="big mono">{acc}<small>%</small></div><div class="cap">표시 {tr["ok"] + tr["bad"]}개 중 {tr["ok"]}개는 추출값이 정답과 같았습니다. 추출 오류는 {tr["bad"]}개입니다.</div></div>'
                    f'<div><h3>추출 실수인가, 서류 차이인가</h3><p class="hs">정답이 있는 {tr["cases"]}건(항공 40, 해상 40)에서 표시 항목의 추출값을 정답과 비교했습니다. 정답은 원문 글자와 쪽 이미지를 직접 읽고 작성했으며 담당자 검토는 거치지 않았습니다.'
                    + (f' 필수값 누락 {m0 + m1}개는 {"모두" if not m1 else f"{m0}개가"} 정답에도 값이 없었습니다.' if m0 + m1 else "")
                    + f'</p><div class="ab">{items}</div></div></div>')

    pm = dg.get("promotion"); promo_html = ""
    if pm:
        items = ""
        for r in pm["rules"]:
            ev = ((f'이력으로 채운 {r["evidence_n"]}개 중 {r["evidence_hit"]}개({_pct(r["evidence_hit"], r["evidence_n"])}%)가 담당자 입력과 같음' if r["id"] == "shipper_history"
                   else f'담당자 등록 {r["evidence_n"]}건 중 {r["evidence_hit"]}건({_pct(r["evidence_hit"], r["evidence_n"])}%)이 이 방식') if r["evidence_n"] else "")
            items += (f'<div class="pr-r" data-rule="{r["id"]}"><div><b>{r["title"]}</b><p>{r["why"]}</p>'
                      f'<span class="ev">{ev} | 적용 대상 {r["covered"]}건</span></div>'
                      f'<button class="pr-b" onclick="promoToggle(\'{r["id"]}\')">규칙으로 승격</button></div>')
        base = {"bad": bad, "ask": ask, "ok": ok}
        cells = "".join(f'<div class="pr-c {k}"><span>{n}</span><b class="mono" id="p-{k}">{base[k]}</b><em id="p-{k}-d"></em></div>'
                        for k, n in (("bad", "오류"), ("ask", "확인"), ("ok", "정상")))
        promo_html = (f'<div class="pr"><div class="pr-l"><h3>KEROL 등록값에서 찾은 규칙</h3>'
                      f'<p class="hs">담당자가 실제로 등록한 값에서 반복되는 판단을 찾았습니다. 규칙으로 승격하면 같은 차이를 더는 오류로 표시하지 않습니다. 모델을 다시 학습시키는 방식이 아니라, 등록 기록이 쌓일수록 검증 규칙이 늘어나는 방식입니다.</p>'
                      f'{items}</div><div class="pr-s"><div class="pr-t">승격 반영 결과</div><div class="pr-g">{cells}</div>'
                      f'<div class="pr-n" id="p-note">승격 전 기준입니다</div></div></div>'
                      f'<script>window.PROMO_BASE={json.dumps(base)};</script>')

    kr = dg.get("kerol"); kerol_html = ""
    if kr:
        cols = ""
        tot_ok = tot_n = 0
        for m, name in (("AIR", "항공"), ("SEA", "해상")):
            rows = ""
            for label, v in kr["fields"][m].items():
                n = sum(v.values()); ok_ = v.get("일치", 0) + v.get("표기 차이", 0); tot_ok += ok_; tot_n += n
                segs = "".join(f'<i style="width:{v.get(k, 0) / n * 100}%;background:{col}" title="{k} {v.get(k, 0)}"></i>'
                               for k, col in (("일치", "#7fa9cf"), ("표기 차이", "#b9cde2"), ("추출 없음", "#d9c08a"), ("다름", "var(--d-bad)")))
                rows += f'<div><span>{label}</span><div class="sb">{segs}</div><span class="m">{_pct(ok_, n)}%</span></div>'
            cols += f'<div><h4>{name}</h4>{rows}</div>'
        sp, sg = kr["src"]["pkg"], kr["src"]["gw"]
        kerol_html = (f'<div class="kr"><div class="kr-l"><div class="big mono">{_pct(tot_ok, tot_n)}<small>%</small></div>'
                      f'<div class="cap">담당자가 KEROL에 직접 등록한 값과 추출값이 같은 비율입니다. {kr["cases"]}건, 항목 {tot_n:,}개 기준.</div>'
                      f'<div class="kr-call"><b>중량과 포장 수는 운송장 값이 기준입니다</b>운송장과 PL 값이 다른 건에서 담당자는 총중량 {sum(sg.values())}건 중 {sg.get("운송장", 0)}건, 포장 수 {sum(sp.values())}건 중 {sp.get("운송장", 0)}건을 운송장 값으로 등록했습니다. 추출기도 운송장 값을 KEROL로 보냅니다.</div></div>'
                      f'<div><h3>KEROL 등록값과 비교</h3><p class="hs">케이스마다 KEROL 목록 조회로 받아 둔 등록값(meta.json)과 비교했습니다. '
                      f'<span class="kl"><i style="background:#7fa9cf"></i>일치 <i style="background:#b9cde2"></i>표기 차이(CIF와 CIP 등) <i style="background:#d9c08a"></i>추출 없음 <i style="background:var(--d-bad)"></i>다름</span></p>'
                      f'<div class="kr-g">{cols}</div></div></div>')

    rr = ""
    for r in rows_all[:14]:
        sub = dg["sub"].get(r["key"]) or []
        subh = ""
        if sub:
            mxs = max(n for _, n in sub)
            subh = '<div class="sub">' + "".join(f'<div><span>{n}</span><div class="sb"><i style="width:{v / mxs * 100}%"></i></div><span class="mono" style="text-align:right">{v}</span></div>' for n, v in sub) + "</div>"
        tv = ""
        if tr and r["key"] in tr["per"]:
            a, b = tr["per"][r["key"]]
            tv = f'<div class="tv">정답 대조 {a + b}개<br><b>{a}</b> 추출 정확, <b>{b}</b> 추출 오류</div>'
        cc = CAUSE_COLOR[r["cause"]]
        rr += (f'<details><summary><span class="ar">›</span><b>{H.escape(str(r["label"]))}</b><span class="chip" style="background:{cc}1f;color:{cc}">{causes[r["cause"]]}</span>'
               f'<span class="cnt mono">{r["cases"]}건</span><span class="sh">{names.get(r["doc"], r["doc"])} | {SHORT.get(r["key"], "")}</span>'
               f'<span class="go" onclick="event.preventDefault();diagGo(\'rule:{r["key"]}\')">해당 건 보기</span></summary>'
               f'<div class="body"><div>{r["explain"]}{subh}</div>{tv}</div></details>')

    def stack(v, n):
        return '<div class="sb">' + "".join(f'<i class="{k}" style="width:{v.get(k, 0) / n * 100}%"></i>' for k in ("bad", "ask", "ok")) + "</div>"
    sh = "".join(f'<div class="shr"><span class="nm" title="{H.escape(str(x["name"]))}">{H.escape(str(x["name"]))}</span>{stack(x, x["n"])}<span class="n mono">{x["n"]}</span>'
                 + (f'<span class="rs">오류 {x.get("bad", 0)}건, 주요 원인 {H.escape(str(x["reason"]))}</span>' if x.get("bad", 0) / x["n"] >= .5 and x["n"] >= 5 else "") + "</div>"
                 for x in dg["ships"])
    fm = "".join(f'<div class="shr"><span class="nm">{H.escape(str(x["name"]))}</span>{stack(x, x["n"])}<span class="n mono">{x["n"]}</span></div>' for x in dg["forms"])

    return f"""<div class="view" id="v-diag"><div class="dg">
{head}{promo_html}{kpi}{mb}
{kerol_html}
<div class="dg-grid">{p1}{p2}{p3}</div>
{acc_html}
<div class="sec-t">검증 항목별 풀이<small>줄을 누르면 설명과 세부 원인이 펼쳐집니다</small></div>
<div class="rl">{rr}</div>
<div class="sec-t">송하인과 운송장 양식<small>오류가 특정 발행처나 양식에 몰리는지 확인합니다</small></div>
<div class="sv"><div class="pn"><h3>송하인 상위 12곳</h3><p class="hs">오류 비율이 절반을 넘는 곳은 주요 원인을 함께 적었습니다.</p>{sh}</div><div class="pn"><h3>운송장 양식</h3><p class="hs">LOTTE, PNS, NX는 좌표 파서로 읽는 항공 양식입니다.</p>{fm}</div></div>
</div></div>"""


JS = r"""
function promoPaint(n){ const b=window.PROMO_BASE; if(!b) return;
  for(const k of ['bad','ask','ok']){ const el=document.getElementById('p-'+k); if(!el) continue; el.textContent=n[k]; const d=n[k]-b[k], em=document.getElementById('p-'+k+'-d');
    em.textContent=d?(d>0?'+':'')+d+'건':''; em.className=d>0?'up':d<0?'dn':''; }
  document.querySelectorAll('.pr-r').forEach(r=>{ const on=PROMO.has(r.dataset.rule); r.classList.toggle('on',on); r.querySelector('.pr-b').textContent=on?'승격됨, 되돌리기':'규칙으로 승격'; });
  document.getElementById('p-note').textContent=PROMO.size?`규칙 ${PROMO.size}개 승격 반영. 상단 건수와 검토 목록에도 적용됩니다`:'승격 전 기준입니다'; }
function promoToggle(id){ PROMO.has(id)?PROMO.delete(id):PROMO.add(id); try{ localStorage.setItem('docx_promo', JSON.stringify([...PROMO])); }catch(e){} refreshCounts(); render(); if(curCase&&document.getElementById('v-confirm').classList.contains('on')) show(curCase,det,'pages'); }
setTimeout(refreshCounts, 0);
function diagGo(f){ document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x.dataset.v==='v-confirm')); document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.id==='v-confirm'));
  filter=f; document.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x.dataset.f===f)); render(); }
"""
