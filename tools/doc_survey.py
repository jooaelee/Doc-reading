#!/usr/bin/env python3
"""모아 둔 서류의 칸 이름을 전수 조사한다. 모델을 부르지 않고 값을 밖으로 내보내지 않는다.

    python3 tools/doc_survey.py [--src 폴더 ...] [--out 폴더]

기본으로 읽는 곳 : work/docs_raw, work/docs_raw_sea
내는 것          : <out>/labels.json, layouts.csv, coverage.txt

왜 필요한가. schema/fields.json 의 문서 출처 22항목은 KEROL SO 양식 60열에서 역산한
것이라 서류에 실제로 인쇄된 칸과 다르다. 상위집합을 정하려면 서류 쪽을 세야 한다.

값을 거르는 방법이 셋이다. 1판은 화주 수만 봤는데, 수하인과 포워더는 자료 전체에서
같은 이름이라 화주를 아무리 세도 안 걸러졌다. 그래서 셋을 겹쳐 쓴다.

1. **글자 크기.** 양식에 인쇄된 칸 이름은 채워 넣은 값보다 작게 찍힌다. 문서마다
   글자 크기의 중앙값을 재서 그보다 작은 것만 후보로 둔다.
2. **화주 수.** 서로 다른 화주 MIN_SHIPPERS 곳 이상에서 나온 것만 남긴다.
3. **가려내기.** 메일 주소와 법인 접미사와 사람 이름꼴은 크기와 화주 수를 통과해도
   버린다. 수하인과 포워더가 여기서 걸린다.

layouts.csv 는 케이스 번호가 들어가므로 저장소에 올리지 않는다.
"""
import collections, csv, hashlib, json, os, re, sys

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("pymupdf 가 필요합니다:  pip install pymupdf")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402
HOME = wuxi_paths.WORK
DEFAULT_SRCS = [("항공", os.path.join(HOME, "docs_raw")), ("해상", os.path.join(HOME, "docs_raw_sea"))]
DEFAULT_OUT = os.path.join(HOME, "survey_out")
MIN_SHIPPERS = 3          # 이 수 미만의 화주에서만 나온 문자열은 버린다
MIN_DOCS = 5              # 문서 수 하한
SAME_FORM = 0.80          # 칸 이름 집합이 이만큼 겹치면 같은 양식으로 본다
SIZE_PCT = 0.75           # 글자 크기가 이 분위 아래인 줄만 칸 이름 후보로 둔다

# 제목에 해당하는 말은 가중치 3, 거들 뿐인 말은 1 로 센다.
MARKERS = [
    ("AWB", [("air waybill", 3), ("airwaybill", 3), ("air consignment note", 3),
             ("chargeable weight", 1), ("airport of departure", 1)]),
    ("BL",  [("bill of lading", 3), ("waybill for combined transport", 3),
             ("port of loading", 1), ("ocean vessel", 1), ("place of receipt", 1)]),
    ("CI",  [("commercial invoice", 3), ("invoice no", 1), ("unit price", 1), ("amount", 1)]),
    ("PL",  [("packing list", 3), ("measurement", 1), ("cbm", 1)]),
]

# fields.json 의 문서 출처 22항목을 찾을 단서.
# 짧은 조각(unit, amount, goods)은 엉뚱한 칸을 물어 오므로 칸 이름 전체와 견준다.
WANT = {
    "hbl_no": ["house air waybill", "house b/l", "hawb", "h.a.w.b", "house bill of lading"],
    "mbl_no": ["master air waybill", "mawb", "m.a.w.b", "master b/l", "m.b/l"],
    "carrier": ["by first carrier", "issuing carrier", "ocean vessel", "carrier's agent"],
    "flight_vessel": ["flight/date", "requested flight", "ocean vessel/voyage", "vessel / voyage"],
    "voyage": ["voyage no", "voy no", "voy. no"],
    "onboard_date": ["on board date", "shipped on board", "laden on board", "sailing on or about"],
    "item_desc": ["nature and quantity of goods", "description of goods", "description of the goods"],
    "package_qty": ["no. of pieces", "number of packages", "no. of p'kgs", "total packages",
                    "no of containers", "quantity"],
    "package_unit": ["kind of package", "type or kind of package", "kind of packages"],
    "gross_weight": ["gross weight", "gross wt", "g/w(kg)", "g.w"],
    "chargeable_weight": ["chargeable weight", "chargeable wt", "volume weight", "chargeable"],
    "net_weight": ["net weight", "net wgt", "n/w(kg)", "n.w"],
    "invoice_amt": ["total amount", "invoice amount", "total value", "grand total", "invoice total"],
    "invoice_cur": ["currency", "curr."],
    "payment_term": ["freight payable at", "payment term", "terms of payment", "payable at"],
    "incoterms": ["incoterm", "terms of delivery", "trade term", "price term"],
    "ref_no": ["reference number", "other reference", "export references", "shipper's reference"],
    "customer_order_no": ["p/o no", "po no", "purchase order", "order no", "customer p/o"],
    "cntr_qty": ["total number of containers", "no. of containers", "no. of cont"],
    "cntr_type": ["container type", "type of container", "size & type of container"],
    "cntr_no": ["container no", "cntr no", "container number", "marks & nos / container"],
    "seal_no": ["seal no", "seal number"],
}

WS = re.compile(r"\s+")
DIGIT = re.compile(r"\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL = re.compile(r"https?://|www\.")
# 법인 접미사. 수하인과 포워더 이름이 여기서 걸린다.
CORP = re.compile(r"(\bco\.?,?\s*ltd|\bltd\b|\bcorp\b|\bcorporation\b|\binc\b|\bllc\b|"
                  r"\bgmbh\b|\bpte\b|\blimited\b|\bcompany\b|\bco\.\s|㈜|주식회사|유한회사)", re.I)
# 주소 꼬리. "11FL, SAMPLE BUILDING", "K7 Lot, Export Processing Zone" 같은 것.
ADDR = re.compile(r"\b(fl\.?|floor|bldg|building|street|st\.|road|rd\.|avenue|ave\.|"
                  r"zone|district|province|city|tower|center|centre|plot|lot)\b", re.I)
# 사람 이름꼴. "A.B.HONG/Gil Dong Hong", "KIM YOUNGHEE".
PERSON = re.compile(r"^[A-Z]\.[A-Z]\.[A-Z]+\s*/|^(kim|lee|park|choi|yoon|jung|han|shin)\s+\w+$", re.I)


def norm(s):
    return WS.sub(" ", s.replace(" ", " ")).strip(" :•-_|")


# 글꼴이 깨졌는지 재는 데 쓰는 낱말. 서류라면 이 중 몇은 반드시 나온다.
DICT = ("the", "of", "and", "date", "name", "address", "weight", "total", "shipper",
        "consignee", "port", "invoice", "description", "quantity", "freight", "carrier",
        "container", "number", "goods", "packing", "list", "price", "gross", "net",
        "code", "origin", "country", "amount", "charge", "no.", "item", "unit")

# 영문에 흔한 세 글자 묶음. 모음 비율이 같아 승부가 안 날 때 이걸로 가른다.
TRI = ("the", "ion", "ing", "ent", "tio", "for", "ver", "ate", "ght", "and", "ter",
       "est", "ers", "con", "pro", "per", "igh", "ess", "ame", "ipt", "cri", "res")


def _score(t):
    """낱말이 몇 개 보이는지 센다. 글꼴이 밀린 채로는 하나도 안 걸린다.

    낱말만으로는 모자란 경우가 있다. 밀린 결과가 대문자로만 나오고 낱말이 붙어
    버리면(NETWEIGHT) 사전이 못 잡는다. 그래서 모음 비율을 같이 본다. 영문은
    자모 중 모음이 3할 안팎이고, 밀린 글자는 모음이 거의 없다.
    """
    low = t.lower()
    n = sum(low.count(w) for w in DICT)
    letters = [c for c in low if c.isalpha()]
    if len(letters) >= 40:
        v = sum(c in "aeiou" for c in letters) / len(letters)
        if 0.25 <= v <= 0.55:
            n += 10
    n += 2 * sum(low.count(g) for g in TRI)
    return n


def unshift(text):
    """글꼴 매핑이 깨져 글자가 통째로 밀린 PDF 를 되돌린다.

    ToUnicode 표가 없는 CID 글꼴에서 나온다. 한 문서 안에서 밀린 폭이 일정하다.
    밀린 결과가 ASCII 안에 떨어지기도 해서(실측 +26, -2) 글자 종류로는 못 가른다.
    그래서 서류에 반드시 나오는 낱말이 몇 개나 보이는지로 잰다.
    """
    if len(text) < 200:
        return text, 0
    base = _score(text)
    if base >= 12:                      # 멀쩡한 문서
        return text, 0
    cand = {26, -26, -2, 2, 6, -6, -24, 24, -817, 817}
    common = collections.Counter(c for c in text if not c.isspace()).most_common(1)
    if common:
        cand |= {ord(common[0][0]) - ord(x) for x in "eoatnsri "}
    best, hits = 0, base
    for off in sorted(cand):
        if not off:
            continue
        try:
            t = "".join(chr(ord(c) + off) if 32 <= ord(c) + off < 127 else " " for c in text[:6000])
        except ValueError:
            continue
        n = _score(t)
        if n > hits:
            best, hits = off, n
    # 통계만 보고 밀면 숫자표 같은 것이 엉뚱하게 밀린다. 되돌린 글에서 실제
    # 낱말이나 영문 세 글자 묶음이 나와야 받아들인다.
    if best:
        t = "".join(chr(ord(c) + best) if 32 <= ord(c) + best < 127 else " " for c in text[:6000])
        low = t.lower()
        words = sum(low.count(w) for w in DICT)
        tri = sum(low.count(g) for g in TRI)
        if words < 2 and tri < 6:
            return text, 0
    if best and hits >= max(8, base * 3):
        return "".join(chr(ord(c) + best) if 32 <= ord(c) + best < 127 else c
                       for c in text), best
    return text, 0


CJK = re.compile(r"[\uac00-\ud7a3\u3040-\u30ff\u4e00-\u9fff]")


def _garbled(t):
    """밀린 글자로 보이는 줄인지 본다.

    한글과 일본어와 한자는 멀쩡한 글이므로 뺀다. 남은 것 중에서 자모에 모음이
    거의 없거나 ASCII 밖 글자로만 이뤄진 줄을 밀린 것으로 본다.
    """
    t = t.strip()
    if len(t) < 4 or CJK.search(t):
        return False
    letters = [c for c in t.lower() if c.isalpha() and ord(c) < 128]
    if len(letters) >= 4:
        return sum(c in "aeiou" for c in letters) / len(letters) < 0.15
    return sum(ord(c) > 127 for c in t) / len(t) > 0.6


def _real_english(t):
    """이미 멀쩡한 영문인지 본다. 낱말이 하나라도 있거나 세 글자 묶음이 둘 이상이면
    건드리지 않는다. 밀린 줄에는 둘 다 안 나온다."""
    low = t.lower()
    return (sum(low.count(w) for w in DICT) >= 1
            or sum(low.count(g) for g in TRI) >= 2)


def _english(t):
    """영문다운 정도. 밀린 줄과 되돌린 줄을 견주는 데 쓴다."""
    low = t.lower()
    n = sum(low.count(w) for w in DICT) * 3 + sum(low.count(g) for g in TRI)
    L = [c for c in low if c.isalpha() and ord(c) < 128]
    if len(L) >= 4:
        v = sum(c in "aeiou" for c in L) / len(L)
        n += 4 if 0.25 <= v <= 0.55 else 0
        # 자모 비율은 눈금으로 준다. 딱 잘라 주면 되돌린 글과 원래 글이 자주 비긴다.
        n += 4 * len(L) / max(len(t), 1)
    return n


def unshift_lines(rows):
    """한 문서 안에서 밀린 줄만 골라 되돌린다.

    문서 전체를 한 덩어리로 재면 안 된다. 실측에서 밀린 것은 멀쩡한 AWB 뒤에
    붙은 중국 통관 신고표뿐이었고, 앞쪽 영문이 멀쩡해서 문서 점수가 높게 나오는
    바람에 795건 중 1건만 되돌아갔다.

    짧은 줄 하나로는 밀렸는지 가리기 어렵다(모음이 우연히 섞인다). 그래서 확실히
    밀린 줄 몇을 씨앗으로 밀린 폭만 알아낸 다음, 그 폭으로 되돌렸을 때 더
    영문다워지는 줄에만 적용한다.
    """
    seed = [t for t, _ in rows if _garbled(t)]
    if len(seed) < 4:
        return rows, 0
    blob = "\n".join(seed)
    while len(blob) < 500:
        blob += "\n" + "\n".join(seed)
    _, off = unshift(blob)
    if not off:
        return rows, 0
    out, n = [], 0
    for t, z in rows:
        if CJK.search(t):
            out.append((t, z))
            continue
        # 자모가 넷 미만인 토막(Q'TY, PPD, EA)은 되돌려도 맞는지 가릴 수 없다
        if _real_english(t) or sum(c.isalpha() and ord(c) < 128 for c in t) < 4:
            out.append((t, z))
            continue
        shifted = "".join(chr(ord(c) + off) if 32 <= ord(c) + off < 127 else c for c in t)
        # WT/VAL 이나 RCP 처럼 짧은 약호는 되돌려도 영문이 안 되므로 절대 기준을 둔다
        if _english(shifted) > _english(t) and _english(shifted) >= 4:
            out.append((shifted, z))
            n += 1
        else:
            out.append((t, z))
    return out, off if n else 0


def is_caption(s):
    """칸 이름처럼 생겼는지 본다. 값이나 이름으로 보이면 버린다."""
    if not (2 <= len(s) <= 64):
        return False
    letters = sum(c.isalpha() for c in s)
    if letters < 2 or letters / len(s) < 0.55:
        return False
    if len(DIGIT.findall(s)) > 2 or s.count(",") > 2:
        return False
    if EMAIL.search(s) or URL.search(s) or CORP.search(s) or ADDR.search(s) or PERSON.match(s):
        return False
    return True


def doctypes(text):
    """한 파일에 CI 와 PL 이 같이 들어오는 일이 잦아 걸린 것을 전부 돌려준다."""
    low, head = text.lower(), text.lower()[:1200]
    out = [name for name, keys in MARKERS
           if sum(w * (2 if k in head and w == 3 else 1) for k, w in keys if k in low) >= 3]
    return out or ["기타"]


def spans_of(doc):
    """(글자, 글자크기) 를 모은다. 칸 이름은 값보다 작게 찍히므로 크기가 단서다."""
    out = []
    for page in doc:
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            for line in blk.get("lines", []):
                # 한 줄 안의 조각을 붙인다. "Gross" 와 "Weight" 가 갈라지는 것을 막는다.
                txt = "".join(sp.get("text", "") for sp in line.get("spans", []))
                sizes = [sp.get("size", 0) for sp in line.get("spans", []) if sp.get("text", "").strip()]
                if txt.strip() and sizes:
                    out.append((txt, round(sum(sizes) / len(sizes), 1)))
    return out


def read_case(case_dir):
    meta = {}
    mp = os.path.join(case_dir, "meta.json")
    if os.path.exists(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:
            pass
    docs = []
    for fn in sorted(os.listdir(case_dir)):
        if not fn.lower().endswith(".pdf"):
            continue
        try:
            with pymupdf.open(os.path.join(case_dir, fn)) as d:
                raw = spans_of(d)
        except Exception as e:
            docs.append((fn, ["열기실패"], set(), 0, str(e)))
            continue
        raw, off = unshift_lines(raw)
        joined = "\n".join(t for t, _ in raw)
        # 큰 글자는 제목이나 채워 넣은 값이다. 다만 중앙값으로 자르면 칸 이름도 3할이
        # 날아가므로(실측에서 MAWB NO 가 통째로 사라졌다) 위쪽 4분의 1만 버린다.
        sizes = sorted(z for _, z in raw if z)
        cut = sizes[int(len(sizes) * SIZE_PCT)] if sizes else 0
        caps = {norm(t) for t, z in raw if z <= cut}
        caps = {c for c in caps if is_caption(c)}
        docs.append((fn, doctypes(joined), caps, off, None if raw else "글자없음"))
    return meta, docs


def families(sets, thr=SAME_FORM):
    """칸 이름 집합이 thr 이상 겹치면 같은 양식으로 묶는다. 딱 맞아떨어질 때만
    같다고 보면 칸 하나 차이로 전부 다른 양식이 되어 종수가 부풀어 오른다."""
    reps, out = [], []
    for s in sets:
        for i, r in enumerate(reps):
            u = len(s | r)
            if u and len(s & r) / u >= thr:
                out.append(i)
                break
        else:
            reps.append(s)
            out.append(len(reps) - 1)
    return out, len(reps)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="서류의 칸 이름을 전수 조사한다. 모델을 부르지 않는다.")
    ap.add_argument("--src", action="append", default=[], metavar="폴더")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    srcs = [(os.path.basename(x.rstrip("/")) or x, x) for x in a.src] or DEFAULT_SRCS
    out = a.out

    cases = []
    for mode, root in srcs:
        if not os.path.isdir(root):
            print(f"  ({root} 없음, 건너뜀)")
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(d):
                cases.append((mode, name, d))
    if not cases:
        sys.exit("서류 폴더를 못 찾았습니다.")
    print(f"케이스 {len(cases)}건을 읽습니다. 잠시 걸립니다.")

    seen = collections.defaultdict(lambda: {"shippers": set(), "docs": 0, "types": collections.Counter()})
    bytype = collections.Counter()
    rows = []
    scanned = failed = empty = shifted = 0

    for i, (mode, name, d) in enumerate(cases, 1):
        if i % 100 == 0:
            print(f"  {i}/{len(cases)}")
        meta, docs = read_case(d)
        shipper = str(meta.get("SHIPPER_NM") or meta.get("SHIPPER") or f"?{name}")
        for fn, dt, caps, off, err in docs:
            if err == "글자없음" or (not caps and not err):
                scanned += 1
                empty += 1
                continue
            if err:
                failed += 1
                continue
            scanned += 1
            if off:
                shifted += 1
            for t in dt:
                bytype[t] += 1
            # 한 문서 안에서 대소문자만 다른 것(Notify Party 와 NOTIFY PARTY)은
            # 한 번만 센다. 안 그러면 어떤 칸의 건수가 문서 수를 넘는다.
            once = {}
            for s in caps:
                once.setdefault(s.lower(), s)
            for key, s in once.items():
                e = seen[key]
                e["shippers"].add(shipper)
                e["docs"] += 1
                for t in dt:
                    e["types"][t] += 1
                e.setdefault("shown", s)
            rows.append({"mode": mode, "case": name, "file": fn, "type": "+".join(dt),
                         "agent": str(meta.get("AGENT_NM") or ""),
                         "carrier": str(meta.get("CARRIER_CD") or ""),
                         "shift": off, "raw": len(caps), "_set": frozenset(x.lower() for x in caps)})

    labels = [{"label": e.get("shown", k), "docs": e["docs"], "shippers": len(e["shippers"]),
               "types": dict(e["types"])}
              for k, e in seen.items()
              if len(e["shippers"]) >= MIN_SHIPPERS and e["docs"] >= MIN_DOCS]
    labels.sort(key=lambda x: -x["docs"])

    keep = {x["label"].lower() for x in labels}
    for r in rows:
        r["_set"] = r.pop("_set") & keep
        r["captions"] = len(r["_set"])

    bykind = collections.defaultdict(list)
    for r in rows:
        bykind[r["type"]].append(r)
    for kind, rs in bykind.items():
        idx, n = families([r["_set"] for r in rs])
        for r, j in zip(rs, idx):
            r["form"] = f"{kind}-{j:03d}"
        bykind[kind] = (rs, n)

    os.makedirs(out, exist_ok=True)
    json.dump({"labels": labels, "scanned": scanned, "cases": len(cases), "empty": empty,
               "shifted": shifted, "min_shippers": MIN_SHIPPERS, "min_docs": MIN_DOCS,
               "bytype": dict(bytype)},
              open(os.path.join(out, "labels.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    with open(os.path.join(out, "layouts.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["mode", "case", "file", "type", "agent", "carrier",
                                           "captions", "raw", "shift", "form"],
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    lower = [(x["label"].lower(), x) for x in labels]
    cov, miss, used = [], [], set()
    for fid, keys in WANT.items():
        hit = [x for low, x in lower if any(k in low for k in keys)]
        for low, x in lower:
            if any(k in low for k in keys):
                used.add(low)
        if hit:
            best = max(hit, key=lambda x: x["docs"])
            cov.append((fid, best["label"], best["docs"], len(hit)))
        else:
            miss.append(fid)
    extra = [x for low, x in lower if low not in used][:80]

    L = [f"케이스 {len(cases)}  서류 {scanned}  열기실패 {failed}  글자없음(스캔본) {empty}"
         f"  글꼴복원 {shifted}",
         f"서류 종류: {dict(bytype)}",
         f"화주 {MIN_SHIPPERS}곳 이상에서 공통으로 나온 칸 이름 {len(labels)}개", "",
         f"=== 양식이 몇 종인가 (칸 이름이 {int(SAME_FORM*100)}% 겹치면 같은 양식) ==="]
    for kind, (rs, n) in sorted(bykind.items(), key=lambda x: -len(x[1][0])):
        caps = sorted(r["captions"] for r in rs)
        big = collections.Counter(r["form"] for r in rs).most_common(1)[0][1]
        L.append(f"  {kind:<12} 서류 {len(rs):>4}건  양식 {n:>4}종  가장 큰 무리 {big:>4}건  "
                 f"칸 수 중앙값 {caps[len(caps)//2]}")
    L += ["", f"=== 우리 22항목 중 칸 이름이 잡힌 것 {len(cov)} ==="]
    for fid, lab, n, k in sorted(cov, key=lambda x: -x[2]):
        L.append(f"  {fid:<20} {lab[:44]:<44} {n}건 (후보 {k})")
    L += ["", f"=== 못 잡은 것 {len(miss)} ==="] + [f"  {f}" for f in miss]
    L += ["", f"=== 우리 목록에 없는 칸 (상위 {len(extra)}) ==="]
    for x in extra:
        L.append(f"  {x['label'][:56]:<56} {x['docs']}건 / 화주 {x['shippers']} / {x['types']}")
    txt = "\n".join(L)
    open(os.path.join(out, "coverage.txt"), "w", encoding="utf-8").write(txt)
    print("\n" + txt + "\n")
    print(f"결과: {out}")
    print("labels.json 과 coverage.txt 는 양식 문구만 남깁니다. 그래도 한 번 훑어 주십시오.")
    print("layouts.csv 는 케이스 번호가 들어 있으니 올리지 마십시오.")


if __name__ == "__main__":
    main()
