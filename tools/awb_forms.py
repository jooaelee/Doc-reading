# -*- coding: utf-8 -*-
"""양식이 고정된 하우스 AWB 를 좌표와 라벨로 읽는다. 모델을 안 부른다.

서류의 56%(397건 중 224건)가 롯데글로벌로지스 HAWB 한 양식이다. 칸의 자리가 고정되어
있어 라벨을 닻으로 삼아 상자 안의 낱말을 모으면 그만이다. 모델이 회차마다 흔들리던
항공사 번호, 편명, 출발지, 대행사 코드가 여기서는 매번 같은 값이다.

    fields = parse(page)   # pymupdf Page. 못 읽는 양식이면 None

돌려주는 키는 docpipe 의 AWB 칸과 같다. 못 읽은 칸은 넣지 않는다. 모델 결과 위에 덮는다.
"""
import re

FORMS = {}
RIGHT = 0.68     # 품명 열이 시작하는 자리. 롯데 양식은 x=428/595 다


def form_of(text):
    """어느 양식인가. 모르면 None."""
    t = text or ""
    if "11FL, YONSEI BUILDING" in t and "SHIPPER'S NAME AND ADDRESS" in t.upper():
        return "LOTTE"
    if "PNS NETWORKS" in t and "Shipper's Name and Address" in t:
        return "PNS"
    if ("TOPPAN FORMS" in t or "AIRCARGO" in t) and "JAPAN" in t and "Chgs" in t:
        return "NX"
    # 그 밖의 포워더 양식(41건)은 자리가 제각각이라 일반 IATA 닻으로 읽으면 송하인 자리에 "NOT NEGOTIABLE" 이
    # 오는 식으로 틀렸다. 모델에 맡긴다. 양식이 늘면 SPECS 에 그 양식만 따로 적는다.
    return None


def lines_of(words, x0=0, x1=10 ** 6, y0=-1, y1=10 ** 6, tol=3.0):
    """상자 안의 낱말을 줄로 묶는다. [(y, [(x, text), ...]), ...] 위에서 아래, 왼쪽에서 오른쪽."""
    picked = [w for w in words if x0 <= w[0] < x1 and y0 < w[1] < y1]
    picked.sort(key=lambda w: (round(w[1] / tol), w[0]))
    out = []
    for w in picked:
        if out and abs(out[-1][0] - w[1]) <= tol:
            out[-1][1].append((w[0], w[4]))
        else:
            out.append((w[1], [(w[0], w[4])]))
    return [(y, sorted(ws)) for y, ws in out]


def text_of(line):
    return " ".join(t for _, t in line[1]).strip()


def _find(words, token, xmax=None, xmin=None):
    for w in words:
        if w[4] == token and (xmax is None or w[0] < xmax) and (xmin is None or w[0] >= xmin):
            return w
    return None


# 양식별 닻. (키, 낱말, x 상한, 이 키보다 아래여야 하는 앞 키)
SPECS = {
    "LOTTE": [("ship", "SHIPPER'S", 100, None), ("cne", "CONSIGNEE'S", 100, None), ("agent", "ISSUING", 100, None),
              ("iata", "AGENT'S", 200, None), ("dep", "AIRPORT", 100, None), ("dest", "Airport", 100, "dep"),
              ("hand", "HANDLING", 100, None), ("pieces", "PIECES", 100, None), ("prepaid", "PREPAID", 100, None)],
    "PNS":   [("ship", "Shipper's", 60, None), ("cne", "Consignee's", 60, None), ("agent", "Issuing", 60, None),
              ("iata", "Agent's", 60, None), ("dep", "Departure(Addr.", 200, None), ("dest", "Destination", 130, "dep"),
              ("hand", "Handling", 60, None), ("pieces", "Pieces", 70, None), ("prepaid", "Prepaid", 100, None)],
    # IATA 표준 배치를 따르는 그 밖의 양식. 낱말은 대소문자를 안 가리고 앞부분만 견준다.
    "IATA":  [("ship", "shipper", 140, None), ("cne", "consignee", 140, "ship"), ("agent", "issuing", 140, "cne"),
              ("iata", "agent", 140, "agent"), ("dep", "airport", 140, "iata"), ("dest", "airport", 160, "dep"),
              ("hand", "handling", 140, "dest"), ("pieces", "pieces", 140, "hand"), ("prepaid", "prepaid", 160, "pieces")],
}


def parse_lotte(page):
    return parse_iata(page, "LOTTE")


def parse_pns(page):
    return parse_iata(page, "PNS")


def parse_iata(page, form):
    words = page.get_text("words")
    full = page.get_text()
    W = page.rect.width
    mid = 0.49 * W
    y = {}
    for key, tok, xmax, after in SPECS[form]:
        ymin = y.get(after, -1) + 1 if after else -1
        if form == "IATA":
            cands = [w[1] for w in words if w[4].lower().startswith(tok) and w[0] < xmax and w[1] > ymin]
        else:
            cands = [w[1] for w in words if w[4] == tok and w[0] < xmax and w[1] > ymin]
        if cands: y[key] = min(cands)          # 낱말 순서는 읽기 순서라 맨 위 것을 고른다
    # 마스터 번호가 라벨 없이 맨 위에 찍히는 양식(PNS). 검증 숫자가 맞는 11자리를 찾는다.
    top_text = " ".join(w[4] for w in words if w[1] < y.get("ship", 60) - 2)
    for m in re.finditer(r"(?<!\d)(\d{3})[\s\-]*(?:[A-Z]{3}[\s\-]*)?(\d{4})[\s\-]*(\d{4})(?!\d)", top_text):
        d = "".join(m.groups())
        if int(d[3:10]) % 7 == int(d[10]):
            top_mawb = d[:3] + "-" + d[3:]; break
    else:
        top_mawb = None
    need = ("ship", "cne", "agent", "dep", "dest", "hand", "pieces", "prepaid")
    if any(k not in y for k in need):
        return None
    out = {}
    # 하우스 번호: 송하인 라벨 위 오른쪽
    top = [w for w in words if w[1] < y["ship"] - 2 and w[0] > mid]
    for w in top:
        if re.fullmatch(r"[A-Z]{2,6}\d{5,12}", w[4]):
            out["hawb"] = w[4]; break
    # 송하인, 수하인 상자
    def block(ya, yb):
        ls = [text_of(l) for l in lines_of(words, x1=mid, y0=ya + 2, y1=yb - 1)]
        # 숫자만 있는 줄(PNS 양식의 "0")과 라벨 줄은 뺀다
        return [l for l in ls if l and not re.fullmatch(r"[\d\s]+", l)
                and not re.match(r"(Copies|It is agreed|Consignee|Shipper's Account)", l)]
    def name_addr(ls):
        # 첫 줄이 담당자나 전화면 이름이 아니다. 그다음 줄을 이름으로 본다.
        idx = next((i for i, l in enumerate(ls) if not re.match(r"(ATTN|TEL|FAX|E-?MAIL|MOBILE|C/O|TE:)", l, re.I)), None)
        if idx is None: return None, None
        rest = ls[:idx] + ls[idx + 1:]
        return ls[idx], (", ".join(rest) or None)
    ship = block(y["ship"], y["cne"])
    if ship:
        out["shipper_name"], out["shipper_address"] = name_addr(ship)
    cne = block(y["cne"], y["agent"])
    if cne:
        out["consignee_name"], out["consignee_address"] = name_addr(cne)
    m = re.search(r"MAWB\s*NO\s*:?\s*(\d{3})\s*-?\s*(\d{8})", full)
    if m:
        out["mawb"] = m.group(1) + "-" + m.group(2)
    elif top_mawb:
        out["mawb"] = top_mawb
    # 대행사 코드: IATA 라벨 아래 한 줄, 왼쪽
    if "iata" in y:
        ls = lines_of(words, x1=mid, y0=y["iata"] + 4, y1=y["iata"] + 26)
        if ls:
            code = text_of(ls[0])
            if re.search(r"\d{4}", code): out["agent_iata_code"] = code
    # 출발지: 라벨 아래, 'to' 줄 위
    to = _find(words, "to", xmax=40)
    y_to = to[1] if to else y["dep"] + 40
    ls = lines_of(words, x1=mid, y0=y["dep"] + 4, y1=y_to - 1)
    if ls: out["origin"] = text_of(ls[0])
    # 노선 줄: 도착 코드, 항공사, 통화, 요금 코드, 신고가액
    ls = [l for l in lines_of(words, y0=y_to + 4, y1=y["dest"] - 1)
          if l[1] and re.fullmatch(r"[A-Z]{3}", l[1][0][1]) and l[1][0][0] < 60]
    if ls:
        line = text_of(ls[0])
        m = re.search(r"\b([A-Z]{3})(USD|JPY|KRW|EUR|CNY|HKD|TWD|SGD|GBP)\s*([\d,]+(?:\.\d+)?)", line)
        if m:
            out["awb_declared_value"] = float(m.group(3).replace(",", ""))
            out["incoterms_hint"] = m.group(1)
        elif re.search(r"\bN\.?V\.?D\b|\bNCV\b", line):
            out["awb_declared_value"] = None
        toks = line.split()
        if toks and re.fullmatch(r"[A-Z]{3}", toks[0]): out["destination_code"] = toks[0]
        # CHGS 코드: PP 는 선불, CC 와 CP 는 착불(운임 착불). FREIGHT COLLECT 글이 없을 때 이것으로 정한다.
        m = re.search(r"\b(USD|KRW|JPY|EUR|CNY|HKD|SGD|TWD)\s+(PP|CC|CP|PC|PX|CX)\b", line)
        if m: out["chgs_code"] = m.group(2)
    # 도착지와 편명
    ls = [l for l in lines_of(words, y0=y["dest"] + 4, y1=y["hand"] - 1) if l[1][0][0] < 200]
    if ls:
        line = text_of(ls[0])
        m = re.search(r"\b([A-Z0-9]{2}\d{2,4})(?:/\d{2}\.[A-Z]{3}\.\d{4}|\s+[A-Z]{3}\.\d{2}\.\d{4})", line)
        if m:
            out["flight1"] = m.group(1)
            out["destination"] = line[:m.start()].strip() or None
        else:
            out["destination"] = " ".join(t for x, t in ls[0][1] if x < 200).strip() or None
    # 요율 줄과 품명
    ls = lines_of(words, y0=y["pieces"] + 8, y1=y["prepaid"] - 1)
    rate = None
    for l in ls:
        t = text_of(l)
        m = re.match(r"^(\d+)\s+([\d,]+\.?\d*)\s+K\s+(?:[A-Z]\s+)?([\d,]+\.?\d*)\s+(?:AS ARRANGED|[\d,.]+)\s*(.*)$", t)
        if m:
            rate = l
            out["awb_pieces"] = int(m.group(1))
            out["awb_gross"] = float(m.group(2).replace(",", ""))
            out["awb_chargeable"] = float(m.group(3).replace(",", ""))
            break
    if rate:
        # 품명: 오른쪽 열(x > 0.7W), 요율 줄부터 PO NO / INV NO / DIM 이 나오기 전까지
        desc = []
        for l in ls:
            if l[0] < rate[0] - 1: continue
            right = " ".join(t for x, t in l[1] if x > RIGHT * W).strip()
            if not right: continue
            if re.match(r"^(PO NO|P/O NO|INV NO|INVOICE NO|DIM|UN \d|CLASS|PACKING GROUP|\"?FREIGHT|SHPT|REF|S/N|HTS|ECCN)", right, re.I):
                break
            desc.append(right)
        if desc:
            out["item_desc"] = " ".join(desc)
    # PO 번호와 인보이스 번호: 라벨 뒤(같은 줄) 또는 다음 줄. 왼쪽 열과 오른쪽 열 어느 쪽에도 찍힌다.
    po, inv = [], None
    want = {"L": None, "R": None}          # 라벨을 봤고 값을 기다리는 중이면 "PO" 또는 "INV"
    for l in ls:
        if rate and l[0] <= rate[0]: continue
        for col, toks in (("L", [t for x, t in l[1] if x < 0.4 * W]), ("R", [t for x, t in l[1] if x > RIGHT * W])):
            t = " ".join(toks)
            if not t: continue
            m = re.match(r"^(P/?O\s*NO\.?|INV(?:OICE)?\s*NO\.?)\s*:?\s*(.*)$", t, re.I)
            if m:
                kind = "PO" if m.group(1).upper().startswith("P") else "INV"
                rest = m.group(2).strip()
                if rest:
                    if kind == "PO": po += [x for x in rest.split() if re.fullmatch(r"[A-Z]{0,3}\d{8,13}", x)]
                    elif inv is None: inv = rest            # JS-CHN-SK HYNIX-260706-C01 처럼 띄어 쓴 번호도 있다
                    want[col] = None
                else:
                    want[col] = kind
                continue
            if want[col] == "PO":
                nums = [x for x in toks if re.fullmatch(r"[A-Z]{0,3}\d{8,13}", x)]
                if nums: po += nums
                elif not re.fullmatch(r"[A-Z]{0,3}\d{8,13}", toks[0]): want[col] = None
            elif want[col] == "INV" and inv is None:
                if not re.match(r"^(DIM|UN |CLASS|\"?FREIGHT|PACKING)", t, re.I): inv = t
                want[col] = None
    if not po:
        # 라벨 없이 찍힌 PO. SK hynix 의 PO 는 4 로 시작하는 열 자리이고 앞에 FCL, FCE, FCF 가 붙기도 한다
        for l in ls:
            if rate and l[0] <= rate[0]: continue
            for _, t in l[1]:
                if re.fullmatch(r"[A-Z]{0,3}4\d{9}", t): po.append(t)
    if po: out["customer_po"] = po[0]
    if inv: out["invoice_no"] = inv
    dims = [[int(a), int(b), int(c), int(n)] for a, b, c, n in re.findall(r"(\d+)\s*X\s*(\d+)\s*X\s*(\d+)\s*CM\s*[=/]\s*(\d+)", full.upper())]
    if dims: out["dims_cm"] = dims
    if re.search(r"FREIGHT\s+COLLECT", full, re.I): out["freight_term"] = "COLLECT"
    elif re.search(r"FREIGHT\s+PREPAID", full, re.I): out["freight_term"] = "PREPAID"
    elif out.get("chgs_code") in ("CC", "CP", "CX"): out["freight_term"] = "COLLECT"
    elif out.get("chgs_code") in ("PP", "PC", "PX"): out["freight_term"] = "PREPAID"
    out["_form"] = form
    return out


def parse_nx(page):
    """일본통운(NX) 양식. 라벨이 인쇄된 종이 위에 값만 찍어서 글자층에 라벨이 없다. 자리가 고정이라 y 띠로 읽는다."""
    import unicodedata
    words = [(w[0], w[1], w[2], w[3], unicodedata.normalize("NFKC", w[4])) for w in page.get_text("words")]
    full = unicodedata.normalize("NFKC", page.get_text())
    out = {}
    top = lines_of(words, y0=60, y1=90)
    if top:
        toks = [t for x, t in top[0][1] if x < 230]
        code = "".join(toks)
        if re.fullmatch(r"[A-Z]{2,5}\d{6,12}", code): out["hawb"] = code
    def block(y0, y1):
        out_ls = []
        for l in lines_of(words, x1=300, y0=y0, y1=y1):
            toks = [t for _, t in l[1]]
            # 계정 번호 칸(SKTE, SCN3 S4002)은 짧은 대문자 토막 한둘이다
            if len(toks) <= 2 and all(re.fullmatch(r"[A-Z0-9]{2,6}", t) for t in toks): continue
            out_ls.append(" ".join(toks))
        return out_ls
    ship = block(95, 158)
    if ship: out["shipper_name"], out["shipper_address"] = ship[0], (", ".join(ship[1:]) or None)
    cne = block(160, 240)
    if cne: out["consignee_name"], out["consignee_address"] = cne[0], (", ".join(cne[1:]) or None)
    m = re.search(r"\bM\s*:\s*(\d{3})\s*-?\s*(\d{4})\s*(\d{4})", full)
    if m: out["mawb"] = m.group(1) + "-" + m.group(2) + m.group(3)
    for l in lines_of(words, x1=120, y0=300, y1=335):
        m = re.fullmatch(r"([A-Z]{3})-([A-Z]{3})", text_of(l))
        if m: out["origin"] = m.group(1); out["destination_code"] = m.group(2); break
    for l in lines_of(words, y0=350, y1=375):
        line = text_of(l)
        m = re.search(r"\b([A-Z0-9]{2}\d{3,4})/\d{2}\b", line)
        if m:
            out["flight1"] = m.group(1)
            out["destination"] = line[:m.start()].strip() or None
            break
    m = re.search(r"\b(USD|JPY|KRW|EUR|CNY)\s+\S+\s+(?:N\.?V\.?D|NCV)\s*([\d,]+(?:\.\d+)?)?", full)
    if m and m.group(2): out["awb_declared_value"] = float(m.group(2).replace(",", ""))
    rate = None
    for l in lines_of(words, y0=440, y1=560):
        t = text_of(l)
        m = re.match(r"^(\d+)\s+([\d,.]+)\s*K\s+[A-Z]?\s*([\d,.]+)\s+AS ARRANGED", t)
        if m:
            rate = l
            out["awb_pieces"] = int(m.group(1)); out["awb_gross"] = float(m.group(2).replace(",", ""))
            out["awb_chargeable"] = float(m.group(3).replace(",", "")); break
    desc = []
    for l in lines_of(words, x0=395, y0=440, y1=640):
        t = text_of(l)
        if re.match(r"^(P/O|PO |INVOICE|INV |ORIGIN|NOT DG|DIM)", t, re.I): break
        desc.append(t)
    if desc: out["item_desc"] = " ".join(desc)
    m = re.search(r"P/?O\s*NO\s*:?\s*([A-Z]{0,3}\d{8,13})", full)
    if m: out["customer_po"] = m.group(1)
    m = re.search(r"INVOICE\s*NO\s*:?\s*\n?\s*([A-Z0-9][A-Z0-9\-_/]{4,})", full)
    if m: out["invoice_no"] = m.group(1)
    dims = [[int(a), int(b), int(c), int(n)] for a, b, c, n in re.findall(r"(\d{3})X(\d{3})X(\d{3})X(\d{3})", full)]
    if dims: out["dims_cm"] = dims
    m = re.search(r"FREIGHT\s*:\s*(COLLECT|PREPAID)", full)
    if m: out["freight_term"] = m.group(1)
    m = re.search(r"\(\s*([\d.]+)\s*M3\)", full)
    if m: out["measurement_cbm"] = float(m.group(1))
    if "hawb" not in out and "mawb" not in out: return None
    out["_form"] = "NX"
    return out


FORMS["LOTTE"] = parse_lotte
FORMS["PNS"] = parse_pns
FORMS["NX"] = parse_nx
FORMS["IATA"] = lambda page: parse_iata(page, "IATA")


def parse(page):
    f = form_of(page.get_text())
    if not f:
        return None
    try:
        return FORMS[f](page)
    except Exception:
        return None
