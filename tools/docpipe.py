# -*- coding: utf-8 -*-
"""서류 꾸러미(AWB + CI + PL ...) 를 KEROL 오더 행으로 바꾸는 파이프라인의 뼈대.

단계
  0. 텍스트 레이어 확인      pymupdf. 글자가 있으면 그대로 쓰고, 없으면 스캔으로 본다.
  1. 페이지 분류            낱말 점수. AWB / LCA / CI / PL / 약관 / 라벨 / 내부서류.
  2. 모델 입력 만들기        필요한 페이지만 고른다. 약관, 중복 라벨, 입고확인서는 뺀다.
  3. 추출 (모델 호출)        JSON 스키마. 이 파일에서는 호출하지 않고 입력 크기와 비용만 센다.
  4. 규칙 검증              수량×단가, 합계, CI↔PL↔AWB 대조, 용적중량, 코드표, 원문 대조.
  5. 오더 행                SO 업로드 60열 중 서류에서 나오는 칸을 채우고 나머지는 비운다.

모델 호출 자리(3) 만 비용이 들고, 나머지는 밀리초 단위다.

쓰는 법
  python3 tools/docpipe.py a.pdf b.pdf                       # 꾸러미마다 쪽 분류와 토큰, 비용
  python3 tools/docpipe.py --bundle 서반=awb.pdf,ci.pdf,pl.pdf  # PDF 여럿을 꾸러미 하나로
  python tools/docpipe.py --codes schema/codes.json --extractions ex.json a.pdf  # 규칙 검증까지

extractions JSON 의 꼴은 verify() 의 키 이름을 따른다. 서류 원본과 추출 JSON 은 저장소에
넣지 않는다. 고객 실명과 번호가 들어 있다.
"""
import json, random, re, shutil, subprocess, sys, tempfile, time, glob, os, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataclasses import dataclass, field
import pymupdf

# ---------- 0. 텍스트 레이어 ----------
@dataclass
class Page:
    file: str
    no: int
    text: str
    n_images: int
    kind: str = "?"
    score: dict = field(default_factory=dict)
    sorted_text: str = ""
    garbled: bool = False
    image: bool = False          # 글자층 대신 그림으로 읽어야 하는 쪽
    dropped: bool = False        # 모델에 안 보내는 쪽. 서류 경계로만 쓴다
    sea: bool = False            # 해상 선하증권(B/L) 쪽. AWB 자리에 들어가되 하우스와 마스터를 가려야 한다
    ocr: str = ""                # 스캔 쪽을 Vision 으로 읽은 글. 종류 가르기와 역할 판정에만 쓴다
    png: str = ""                # 그려 둔 그림 경로
    routed: str = ""
    ocr_engine: str = ""

    @property
    def needs_image(self):
        return self.kind == "SCAN" or self.image

    @property
    def is_scan(self):
        return len(self.text.strip()) < 40 and self.n_images > 0

# 글자층 보정. 실측으로 정한다. 켜고 끈 잡음을 견주어 좋아진 것만 남긴다.
#   nfkc     전각을 반각으로 민다. 일본 사무소의 AWB 가 번호를 ＮＥＣ ４６１６ 처럼 찍는다.
#   garbled  ToUnicode 표가 없는 글꼴로 통째로 밀린 쪽(&RPSDQ\ 가 Company)은 그림으로 읽는다.
#            되돌리기는 답이 아니다. 같은 쪽 안에서 글꼴마다 밀린 폭이 다르고(+29 와 +30),
#            숫자는 제어 문자 자리로 밀려 글자층에서 아예 사라진다. 케이스 3의 CI 가
#            그랬고 모델은 금액을 AWB 의 신고가액에서 지어냈다. 578건 중 82건이 이런 쪽이다.
#   awb_sort AWB 쪽만 위에서 아래, 왼쪽에서 오른쪽 순서로 다시 뽑는다. 인쇄 순서로는
#            수하인 칸이 송하인 칸 앞에 올 때가 있다. 문서 전체를 정렬하면 표가 흩어져
#            더 나빠졌으므로(62.9%) AWB 에만 건다.
# table_sort 는 기본으로 끈다. 하이닉스 판(h1)에서 set_prompt 가 켠다.
TEXT_FIX = {"nfkc": True, "garbled": True, "awb_sort": True, "table_sort": False}


def fix_text(t):
    # 널 바이트와 제어 문자는 뺀다. 프롬프트로 넘길 때 "embedded null byte" 로 죽었다(배치 397건 중 1건).
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    if TEXT_FIX.get("nfkc"):
        t = unicodedata.normalize("NFKC", t)
    return t


def is_garbled(t):
    """밀린 글꼴로 찍힌 쪽인지 본다. 자모가 넷 이상인 줄 중 모음이 거의 없는 줄의 몫으로 잰다."""
    import doc_survey
    # 자모가 넷 이상인 줄만 센다. 숫자와 기호만 있는 줄('$7(', 321R)은 밀렸는지 가릴 수 없어
    # 분모에 넣으면 비율이 떨어져 못 잡는다(케이스 05). 밀린 폭이 크면(-817) 그리스 글자로
    # 찍히는데 그것도 자모라서 같이 센다. 한중일 글자는 멀쩡한 글이라 뺀다.
    lines = [ln for ln in t.split("\n")
             if sum(c.isalpha() for c in ln) >= 4 and not doc_survey.CJK.search(ln)]
    if len(lines) < 2:
        return False
    bad = sum(1 for ln in lines if doc_survey._garbled(ln))
    # 줄이 적은 쪽(포장명세서의 총계 쪽)은 더 엄하게 본다. 라벨 한 장이 밀렸다고 나오면 안 된다.
    return bad / len(lines) >= (0.4 if len(lines) >= 5 else 0.75)


def load_pdf(path):
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        # 깨진 PDF(해상 서류 한 건이 "no objects found"). 꾸러미의 다른 파일은 그대로 읽는다.
        sys.stderr.write(f"읽지 못한 PDF 를 건너뜁니다: {os.path.basename(path)[:20]}... ({str(e)[:60]})\n")
        return []
    out = []
    for i, p in enumerate(doc):
        pg = Page(path, i + 1, fix_text(p.get_text()), len(p.get_images()))
        pg.sorted_text = fix_text(p.get_text("text", sort=True))
        pg.garbled = bool(TEXT_FIX.get("garbled")) and is_garbled(pg.text)
        out.append(pg)
    return out


def find_mawb(text):
    """AWB 원문에서 마스터 번호를 찾는다. 3자리 항공사 코드 뒤에 공항 코드가 끼기도 한다(180 ICN 5410 2893).
    검증 숫자(앞 7자리를 7로 나눈 나머지 = 마지막 자리)가 맞는 것만 받는다. 모델이 이 꼴을 자주 비워 냈다."""
    for m in re.finditer(r"(?<!\d)(\d{3})[\s\-]*(?:[A-Z]{3}[\s\-]*)?(\d{4})[\s\-]*(\d{4})(?!\d)", text or ""):
        d = "".join(m.groups())
        if int(d[3:10]) % 7 == int(d[10]):
            return d[:3] + "-" + d[3:]
    return None


def parse_awb_form(page):
    """양식 파서(`awb_forms.py`)로 AWB 쪽을 읽는다. 양식을 모르거나 실패하면 None."""
    try:
        import awb_forms
        doc = pymupdf.open(page.file)
        return awb_forms.parse(doc[page.no - 1])
    except Exception:
        return None


def find_hawb(text, mawb=None):
    """AWB 원문에서 하우스 번호를 찾는다. 모델이 하우스 자리에 마스터를 적어 비워졌을 때만 쓴다.
    글자 두어 개 뒤에 숫자가 이어지는 토큰(실행사 접두 영문 뒤에 숫자가 붙은 하우스 번호 모양)을 고른다."""
    mdig = re.sub(r"\D", "", str(mawb or ""))
    for m in re.finditer(r"(?<![A-Z0-9])([A-Z]{2,5}\d{6,12})(?![A-Z0-9])", text or ""):
        tok = m.group(1)
        if tok.startswith(("USCI", "TEL", "VAT", "FAX", "UN", "HS")): continue
        if mdig and re.sub(r"\D", "", tok) == mdig: continue
        return tok
    return None


def apply_awb_sort(pages):
    """분류가 끝난 뒤 글을 정렬본으로 바꾼다. AWB 는 늘, 인보이스와 포장명세서는 table_sort 일 때.

    🔴 왜 인보이스와 포장명세서에도 거나. 하이닉스 서류(인보이스 4장이 든 우시 표본)는 표의 열 머리글이
       기본 추출 순서에서 값보다 **뒤에** 나온다. 「Sap Code / Po No. / Po Item No.」 가
       맨 끝에 몰려 찍히고 값은 그 앞에 따로 몰린다. 그 글을 받은 모델이 인보이스마다
       열을 다르게 짚었다. 1장은 sap_code 에 P3103668, 2장은 같은 자리에 4303400871 을
       넣었다. 프롬프트를 아무리 좁혀도 안 고쳐진다. 글에 열이 안 남아 있어서다.

       정렬본은 좌표 순으로 펴서 머리글 줄과 값 줄이 세로로 맞는다. 고칠 자리가 입력이다.

       기본으로 끄는 이유는 다른 판의 실측값이 기본 추출 글로 잰 값이라서다. 켜고 끄는
       것을 조건으로 두면 그 차이도 숫자로 잴 수 있다."""
    if TEXT_FIX.get("awb_sort"):
        for p in pages:
            if p.kind == "AWB" and getattr(p, "sorted_text", None):
                p.text = p.sorted_text
    if TEXT_FIX.get("table_sort"):
        for p in pages:
            if p.kind in ("CI", "PL") and getattr(p, "sorted_text", None):
                p.text = p.sorted_text
    return pages

# ---------- 1. 페이지 분류 ----------
KIND_WORDS = {
    "AWB":   ["air waybill", "airport of departure", "shipper's name and address", "mawb", "hawb",
              "not negotiable", "waybill doc", "chargeable", "flight", "airline",
              "n.v.d", "ncv", "freight collect", "freight prepaid", "as arranged",
              # 해상 선하증권. 하우스 B/L 이 인보이스나 포장명세서로 갈리던 것을 막는다(해상 30건 중 17건)
              # 항구, 선박 같은 낱말은 인보이스와 포장명세서에도 찍혀서 넣지 않는다(항공 인보이스가 AWB 로 갈렸다)
              "bill of lading", "b/l no", "laden on board", "shipped on board", "check b/l", "waybill no",
              "multimodal transport", "shipper's load"],
    "LCA":   ["lca code", "oversea handling information", "exrate", "grand total (usd)"],
    "CI":    ["commercial invoice", "invoice no", "invoice number", "unit price", "total amount",
              "amount", "sold to", "invoice date", "hs code", "payment term", "quantity", "description"],
    "PL":    ["packing list", "weight memo", "gross weight", "net weight", "g/w", "n.weight",
              "carton", "packages", "dimension", "p'kg", "m3", "l w h", "@w/t"],
    "TERMS": ["conditions of contract", "limitation of liability", "warsaw", "montreal",
              # 선하증권 뒷면 약관(스캔). 해상 15번이 이 한 쪽 때문에 전체 스키마 호출을 하나 더 했다
              "both-to-blame", "himalaya", "general average", "hague rules", "cogsa", "definitions"],
    "LABEL": ["express worldwide", "*gls certified label*", "license plate"],
    "INTERNAL": ["交易明细表", "入库确认书", "送货场所", "出库单"],
    "PO":    ["purchase order", "order qty", "delivery terms", "vendor", "purchaser", "total order amount",
              "발주번호", "발주일자", "구매자", "납품장소", "인도조건", "발주담당"],
    # 중국 통관 신고표. 밀린 글꼴에서는 낱말이 붙어 나오므로(ChineseName) 붙은 꼴도 같이 둔다.
    "CUSTOMS": ["goods photo", "chinese name", "brand", "maker", "model/specification", "use for",
                "new,old or repair", "china customs clearance", "export declaration hs code", "skhynix systemic code",
                "chinesename", "goodsphoto", "countryoforigin", "materialpicture", "equipmentmodelno",
                "customsclearance", "importgoodsinformation",
                # 위험물 수납검사증과 냉동 컨테이너 점검표. 선박명과 항구가 찍혀 선하증권으로 갈리면 빈 값이 이긴다
                "packing certificate of dangerous goods", "declaration of dangerous goods", "위험물컨테이너",
                "cntr operation report", "container inspection sheet", "수납검사증"],
    "PREALERT": ["flight information", "shipping details as follows", "ご手配", "cargo manifest",
                 "pre-alert", "prealert", "出荷依頼", "通関", "スケジュール"],
}
# 더 특이한 낱말에 가중치. 흔한 낱말(amount, flight)은 1점.
WEIGHT = {"air waybill": 3, "lca code": 5, "oversea handling information": 5, "commercial invoice": 4,
          "packing list": 4, "weight memo": 4, "conditions of contract": 6, "express worldwide": 4,
          "交易明细表": 6, "入库确认书": 6, "flight information": 4, "cargo manifest": 5,
          "shipper's name and address": 3, "airport of departure": 3, "waybill doc": 4,
          "invoice no": 2, "payment term": 2, "purchase order": 8, "total order amount": 4,
          "발주번호": 6, "발주일자": 4, "납품장소": 4, "인도조건": 3,
          "goods photo": 5, "chinese name": 5, "china customs clearance": 6, "new,old or repair": 4,
          "export declaration hs code": 4, "skhynix systemic code": 4, "model/specification": 3,
          "chinesename": 5, "goodsphoto": 5, "countryoforigin": 1, "materialpicture": 4, "equipmentmodelno": 4,
          "customsclearance": 5, "importgoodsinformation": 5, "n.v.d": 2, "as arranged": 2, "p'kg": 2, "l w h": 2, "出荷依頼": 3,
          "bill of lading": 4, "b/l no": 3, "check b/l": 3, "shipper's load": 2,
          "laden on board": 2, "shipped on board": 2, "waybill no": 2, "multimodal transport": 2,
          "packing certificate of dangerous goods": 8, "declaration of dangerous goods": 8, "위험물컨테이너": 8,
          "cntr operation report": 8, "container inspection sheet": 8, "수납검사증": 8}
# 선하증권 판정 낱말. "port of loading" 만으로는 안 된다. 항공 수입 인보이스(TEL)가 (ETD / Port of Loading) 라벨을 찍는다.
SEA_WORDS = re.compile(r"bill of lading|b/l no|check b/l|ocean vessel|vessel\s*/\s*voy|multimodal transport|on board date|door/door|\d\s*x\s*\d\d(?:gp|hc|hq|rh|rf|dc)\b")
AIR_WORDS = re.compile(r"air waybill|airport of|\(hawb\)|\(mawb\)|flight/date|air consignment")

_HINT_WORDS = None


def unshift_hint(t):
    """밀린 쪽의 종류를 가르려고 되돌려 본다. 값을 읽는 데는 안 쓴다(숫자가 사라진다).

    폭을 하나씩 대 보고 서류 낱말(commercial invoice, packing list ...)이 가장 많이 나오는
    폭을 고른다. 실측 폭은 -29, -30, -2, 26, -817 이었고 한 쪽 안에서 글꼴마다 다르다.
    """
    global _HINT_WORDS
    if _HINT_WORDS is None:
        _HINT_WORDS = [w for ws in KIND_WORDS.values() for w in ws if w.isascii()]
    best, hits = t, sum(1 for w in _HINT_WORDS if w in t.lower())
    for off in list(range(-40, 41)) + [-817, 817]:
        if off == 0: continue
        u = "".join(c if c.isspace() else (chr(ord(c) - off) if 32 <= ord(c) - off < 127 else c) for c in t)
        n = sum(1 for w in _HINT_WORDS if w in u.lower())
        if n > hits:
            best, hits = u, n
    return best


def classify(page: Page):
    t = page.text.lower()
    if page.is_scan:
        page.kind = "SCAN"; return page
    if page.garbled:
        # 밀린 글꼴 쪽은 그림으로 읽되, 종류는 되돌린 글로 가른다. 그래야 인보이스와 포장명세서를
        # 따로 부를 수 있다. 12쪽을 한 번에 그림으로 주니 포장명세서 총계를 빠뜨렸다.
        page.image = True
        t = unshift_hint(page.text).lower()
    sea_ctx = bool(re.search(r"vessel|voyage|port of loading|port of discharge|ocean|\bb/l\b|container", t))
    # OCR 은 낱말 사이 공백을 자주 먹는다(BILL OFLADING). 공백을 뺀 글로도 낱말을 찾는다
    t_ns = re.sub(r"\s+", "", t)
    if not sea_ctx:
        sea_ctx = bool(re.search(r"vessel|voyage|portofloading|portofdischarge|ocean|b/l|container", t_ns))
    for kind, words in KIND_WORDS.items():
        # "bill of lading" 은 항공 서류에도 찍힌다(Export Bill of Lading No, 위험물 신고서). 배, 항구 낱말이 같이 있을 때만 센다
        page.score[kind] = sum(WEIGHT.get(w, 1) for w in words if (w in t or (" " in w and w.replace(" ", "") in t_ns))
                               and (sea_ctx or w not in ("bill of lading", "shipper's load", "multimodal transport", "b/l no")))
    # 제목이 가장 센 단서다. 인보이스 둘째 쪽에 (HAWB)(MAWB) 라벨이 찍히거나, 인보이스에 "Export B/L No" 가
    # 찍혀 선하증권으로 갈리던 쪽이 있었다(해상 06, 16). 첫머리에 제목이 있으면 그 종류에 얹어 준다.
    head = t[:260]
    ci_title = re.search(r"(?:commercial|non[\s-]*commercial|proforma|import|export|tax)\s+invo[il1]ce|^\s*invo[il1]ce\s*$", head, re.M)
    pl_title = re.search(r"packing\s*(?:list|slip)|weight\s*memo", head)
    bl_title = re.search(r"bill\s+of\s+lading|sea\s*waybill|check\s*b/l", head)
    if ci_title and not pl_title and not bl_title:
        page.score["CI"] = page.score.get("CI", 0) + 5
    elif pl_title and not ci_title and not bl_title and not re.search(r"invo[il1]ce\s*(?:&|/|and)\s*packing", head):
        page.score["PL"] = page.score.get("PL", 0) + 5
    elif bl_title and not ci_title:
        page.score["AWB"] = page.score.get("AWB", 0) + 5
    best = max(page.score, key=page.score.get)
    if best == "PO" and re.search(r"packing list|commercial invoice|packing slip|invoice no", t):
        # 포장명세서에도 "Purchase Order No:" 가 찍힌다. 제목이 인보이스나 포장명세서면 그쪽이다.
        page.score["PO"] = 0
        best = max(page.score, key=page.score.get)
    if best == "CI" and page.score.get("PL") == page.score["CI"] and "packing" in t:
        best = "PL"                       # 비기면 제목이 가른다. 포장명세서에도 단가 낱말이 찍힌다
    # 밀린 글꼴 쪽은 되돌린 글이 성기다. 낱말이 둘만 보여도 받는다.
    floor = 2 if page.image else 3
    if best == "CUSTOMS" and page.score["CUSTOMS"] < 5:
        # 통관표로 버리는 것은 확실할 때만. 수리 인보이스가 countryoforigin 하나로 버려진 적이 있다.
        page.score["CUSTOMS"] = 0
        best = max(page.score, key=page.score.get)
    if best == "AWB" and page.score.get("CI", 0) == page.score["AWB"] and re.search(r"invoice no", t):
        best = "CI"                       # 인보이스의 "Hynix Export B/L No" 칸이 선하증권 점수와 비겼다
    if best == "AWB" and re.search(r"declaration for dangerous goods|dangerous goods data|dangerous goods declaration", t) \
            and not re.search(r"air waybill|airport of departure|bill of lading no|b/l no", t):
        best = "OTHER"                    # 위험물 신고서. 항공과 해상 낱말이 다 찍힌다
    page.kind = best if page.score.get(best, 0) >= floor else "OTHER"
    page.sea = page.kind == "AWB" and bool(SEA_WORDS.search(t) or re.search(r"billoflading|b/lno|checkb/l|oceanvessel|multimodaltransport|onboarddate", t_ns)) and not AIR_WORDS.search(t)
    return page

# ---------- 2. 모델 입력 ----------
DROP = {"TERMS", "INTERNAL", "PO", "CUSTOMS"}    # 모델에 보낼 필요가 없는 쪽. 구매 주문서는 인보이스와 헷갈려 총액을 두 배로 만들었다
# 🔴 하이닉스 판은 통관 용도설명서를 버리지 않는다. 하이닉스가 꼭 필요하다고 한 셋 중 하나가
#    Material 인데, 인보이스에 그 열이 없는 서류가 절반이다. 그런데 같은 묶음의 용도설명서에는
#    품목마다 「SKhynix SAP Code」 가 인쇄돼 있다(실측: 수리품 인보이스 표본 5쪽). 버리던 쪽에 답이
#    있었다. 다른 판에서는 그대로 버린다. 그 판은 Material 을 안 쓰고 쪽만 늘어난다.
DROP_H1 = {"TERMS", "INTERNAL", "PO"}
KEEP_ONE = set()                      # 종류가 같다고 하나만 남기지 않는다. DHL 라벨은 세 쪽의 글이 다르다

def drop_kinds():
    return DROP_H1 if PROMPT_VERSION == "h1" else DROP


def select_pages(pages):
    out, seen = [], set()
    drop = drop_kinds()
    for p in pages:
        if p.kind in drop: continue
        if p.kind in KEEP_ONE:
            if p.kind in seen: continue
            seen.add(p.kind)
        # 완전히 같은 텍스트가 반복되면(라벨 2장) 하나만
        if not p.needs_image:
            key = (p.kind, p.text.strip()[:400])
            if key in seen: continue
            seen.add(key)
        out.append(p)
    return out

def est_tokens_text(s):
    # 영문/숫자 위주 서류는 4자에 1토큰 남짓. CJK 는 글자당 1토큰 가까이.
    cjk = sum(1 for ch in s if ord(ch) > 0x2E80)
    return int((len(s) - cjk) / 4 + cjk)

def est_tokens_image(w, h):
    return int(w * h / 750)   # Claude 비전 토큰 공식

def render_scan(page: Page, dpi=150):
    doc = pymupdf.open(page.file)
    pix = doc[page.no - 1].get_pixmap(dpi=dpi)
    # 긴 변 1568px 이하로 맞추면 토큰이 ~1,600 에서 끝난다
    return pix.width, pix.height, pix.tobytes("png")

# 단가 (USD / MTok)  2026-09 기준
# sonnet-5 에 (3.0, 15.0) 을 적어 두고 있었다. 그것은 sonnet-4.6 의 값이고 sonnet-5 는
# $2/$10 이다. 지금까지 나온 usd_sonnet 열은 모두 1.5배로 부풀어 있었다.
PRICE = {"haiku-4.5": (1.0, 5.0), "sonnet-5": (2.0, 10.0), "opus-5": (5.0, 25.0)}
SCHEMA_TOKENS = 1200     # 시스템 프롬프트 + JSON 스키마 + 코드표. 캐시하면 0.1배 값
OUTPUT_TOKENS = 900      # 60열 JSON + 근거 문자열

def cost(in_tok, out_tok=OUTPUT_TOKENS, model="haiku-4.5", cached_schema=True):
    pi, po = PRICE[model]
    sch = SCHEMA_TOKENS * (0.1 if cached_schema else 1.0)
    return ((in_tok + sch) * pi + out_tok * po) / 1e6


# ---------- 3. 추출 (모델 호출) ----------
# llama-server, Ollama, vLLM 처럼 OpenAI 호환 /v1/chat/completions 를 내는 서버와
# Anthropic Messages API 둘 다 같은 스키마로 부른다. 회사 서버(온프레미스)와
# 클라우드가 코드 한 벌로 바뀐다.
import base64, urllib.request

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "hawb": STR, "mawb": STR, "invoice_no": STR, "incoterms": STR, "currency": STR,
        "origin": STR, "destination": STR, "flight1": STR, "customer_po": STR, "item_desc": STR,
        "package_unit": STR,
        "ci_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "properties": {"description": STR, "qty": NUM, "unit_price": NUM, "amount": NUM},
                     "required": ["description", "qty", "unit_price", "amount"]}},
        "ci_total": NUM, "ci_gross": NUM, "ci_net": NUM,
        "pl_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "properties": {"description": STR, "qty": NUM}, "required": ["description", "qty"]}},
        "pl_packages": NUM, "pl_gross": NUM, "pl_net": NUM,
        "awb_pieces": NUM, "awb_gross": NUM, "awb_chargeable": NUM, "awb_declared_value": NUM,
        "dims_cm": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "properties": {"l": NUM, "w": NUM, "h": NUM, "n": NUM}, "required": ["l", "w", "h", "n"]}},
        "lca_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                      "properties": {"code": STR, "issue_amt": NUM, "exrate": NUM, "usd_amt": NUM},
                      "required": ["code", "issue_amt", "exrate", "usd_amt"]}},
        "lca_total_issue": NUM, "lca_grand_total_usd": NUM,
    },
    "required": ["hawb", "mawb", "invoice_no", "incoterms", "currency", "origin", "destination", "flight1",
                 "customer_po", "item_desc", "package_unit", "ci_lines", "ci_total", "ci_gross", "ci_net",
                 "pl_lines", "pl_packages", "pl_gross", "pl_net", "awb_pieces", "awb_gross", "awb_chargeable",
                 "awb_declared_value", "dims_cm", "lca_lines", "lca_total_issue", "lca_grand_total_usd"],
}

PROMPT_VERSION = "v3"   # v1: 일반 규칙만. v2: 소형 모델이 틀린 자리를 규칙으로 막는다. v3: 거래처 이름과 주소를 더한다.
# 04 문서의 실측은 전부 v2 로 잰 것이다. 그때와 똑같이 돌리려면 --prompt v2 를 준다.
def strict_schema(sc):
    """["number","null"] 같은 널 유니온을 단일 타입으로 바꾼 사본. 값이 없으면 모델이 빈 문자열이나 0 을 준다."""
    if isinstance(sc, dict):
        out = {}
        for k, v in sc.items():
            if k == "type" and isinstance(v, list): out[k] = [t for t in v if t != "null"][0]
            elif k in ("additionalProperties",): continue
            else: out[k] = strict_schema(v)
        return out
    if isinstance(sc, list): return [strict_schema(x) for x in sc]
    return sc
SCHEMA_STRICT = strict_schema(SCHEMA)

# v3: 거래처 이름과 주소. 이 값이 있어야 KEROL 코드를 찾을 수 있다(`tools/codefind.py`).
PARTY_PROPS = {"shipper_name": STR, "shipper_address": STR, "consignee_name": STR, "consignee_address": STR}
SCHEMA_V3 = {**SCHEMA, "properties": {**SCHEMA["properties"], **PARTY_PROPS},
             "required": SCHEMA["required"] + list(PARTY_PROPS)}
SCHEMA_V3_STRICT = strict_schema(SCHEMA_V3)

# v4: 검증을 걸 수 있는 칸만 더한다. 뽑기만 하고 아무도 안 보는 칸은 프롬프트만 길어지고
# 기존 칸의 정확도를 갉는다. 그래서 보태는 기준을 하나로 뒀다. KEROL 로 가거나, 규칙
# 하나를 새로 세우거나 둘 중 하나여야 한다.
CHECK_PROPS = {
    "agent_iata_code": STR,            # 대행사 계정 번호와 뒤바뀌는 일이 실제로 있었다
    "agent_account_no": STR,
    "freight_term": STR,               # PREPAID 또는 COLLECT. 인코텀즈와 어긋나면 잡는다
    "awb_weight_charge": NUM,
    "awb_valuation_charge": NUM,
    "awb_tax": NUM,
    "awb_other_charges_agent": NUM,
    "awb_other_charges_carrier": NUM,
    "awb_total": NUM,                  # 선불이면 Total Prepaid, 착불이면 Total Collect
    "measurement_cbm": NUM,            # 치수 합과 맞는지 본다
}
# 포장명세서 줄마다 중량이 적히면 합이 총계와 맞아야 한다.
PL_LINE_V4 = {"type": "array", "items": {"type": "object", "additionalProperties": False,
              "properties": {"description": STR, "qty": NUM, "net": NUM, "gross": NUM},
              "required": ["description", "qty", "net", "gross"]}}
SCHEMA_V4 = {**SCHEMA_V3,
             "properties": {**SCHEMA_V3["properties"], **CHECK_PROPS, "pl_lines": PL_LINE_V4},
             "required": SCHEMA_V3["required"] + list(CHECK_PROPS)}
SCHEMA_V4_STRICT = strict_schema(SCHEMA_V4)


# ---------- 3.4 하이닉스 중국법인 판 (h1) ----------
# 🔴 왜 판을 가르나. 우리 판(v4)은 KEROL SO 한 행을 채우는 것이 목적이라 줄 단위 값을
#    최소로 뽑는다. 인보이스 줄에서 품명과 수량과 단가와 금액 넷만 받고, 줄이 어느 PO 의
#    어느 자재인지는 안 받는다. 건 단위 `customer_po` 에 PO 하나만 담는다.
#
#    하이닉스 중국법인(우시, 충칭)이 쓰는 자리는 다르다. GERP 의 「Change Bill of Lading:
#    General1 (China)」 두 번째 표가 BL 에 실린 PO 별 품목을 받는데, 열이 Imp.Req. No.,
#    Item, Purchase Order, Material, Vendor Part No., B/L quantity, Invoice amount,
#    Net Price 다. 줄마다 PO 와 자재 코드가 있어야 채워진다. 인보이스 한 장에 PO 번호가
#    열세 개 나열되고 줄이 열다섯인 서류가 실제로 있다(`02-GERP-PO-매핑.md` 4장).
#    건 단위 PO 하나로는 그 표의 한 줄도 못 채운다.
#
# 🔴 왜 v4 에 얹지 않고 따로 두나. v4 의 「보태는 기준」 을 지킨다. KEROL 로 가거나 규칙
#    하나를 새로 세우거나 둘 중 하나여야 칸을 늘린다. 줄 단위 PO 와 자재 코드는 KEROL 로
#    가지 않는다. 품목은 KEROL 관리 대상이 아니다. 그러니 우리 판에 넣으면 아무도 안 보는
#    칸이 늘고 기존 칸의 정확도만 갉는다. 받는 곳이 다르므로 판을 가른다.
#
# 🔴 못 채우는 열을 밝혀 둔다. Imp.Req. No. 와 Item 은 GERP 의 수입의뢰 쪽에서 오고
#    선적 서류에 없다. 서류에서 나오는 것은 Purchase Order, Material, Vendor Part No.,
#    B/L quantity, Invoice amount, Net Price 여섯이다. 여덟 중 여섯이고, 나머지 둘은
#    하이닉스가 자기 시스템에서 잇는다. 이 경계를 표에도 적는다(`tools/gerp_xlsx.py`).
# 🔴 칸 이름을 서류에 인쇄된 열 이름으로 둔다. 처음에는 `material_code` 와 `part_no` 로
#    뜻을 붙여 받았는데, 실제 서류(인보이스 4장이 든 우시 표본)의 열이 Sap Code, Po No., Po Item No.,
#    SK Hynix I/V No., HIOS No. 다섯이었다. 다섯 중 어느 것이 GERP 의 Material 인지는
#    서류만 보고 정할 수 없고 하이닉스가 정한다. 우리가 뜻을 붙이면 그 판단을 우리가
#    대신한 것이 되고, 틀리면 ERP 로 틀린 코드가 들어간다. 그래서 인쇄된 이름대로 받고,
#    GERP 열로 잇는 자리는 표 한 곳(`tools/gerp_xlsx.py` 의 GERP_MAP)에 모은다.
#
#    열 이름이 다른 공급사를 위해 일반 이름 둘(material_code, part_no)을 남겨 둔다.
#    위 다섯 중 어느 것도 인쇄되지 않았을 때만 채운다.
# 🔴 필수는 늘 찍히는 칸만 둔다. 나머지는 스키마에 두되 필수에서 뺀다. 필수로 두면 모델이
#    없는 칸도 빈 값으로 채워 내보내고, 그만큼 출력 토큰이 늘어 호출이 길어진다. 실측에서
#    한 꾸러미의 벽시계는 가장 오래 걸린 호출 하나가 정한다. 그 호출이 열세 줄짜리 인보이스였다.
# 🔴 ’26.09.30 하이닉스 우시 물류팀이 요구 항목 열 개를 확정해 보내 왔다(임지원 PM 메일).
#    거기서 두 가지가 우리 판과 어긋났다.
#    1. Material No 와 SK HYNIX Sap Code 를 **따로** 달라고 한다. 우리는 material_code 를
#       「위 다섯 열이 하나도 안 찍혔을 때만」 채우게 두었다. 실제 시험 서류에는 MATERIAL 열과
#       SAP Code 열이 같은 표에 나란히 찍힌다. 조건을 풀어 둘 다 받는다.
#    2. SERIAL NUMBER 를 요구하는데 우리 스키마에 아예 없었다. 더한다.
HYNIX_CODES = {"sap_code": STR, "po_no": STR, "po_item_no": STR, "iv_no": STR, "hios_no": STR,
               "material_code": STR, "serial_no": STR, "part_no": STR}
HYNIX_CI_LINE = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                 "properties": {"seq": NUM, "invoice_no": STR, **HYNIX_CODES,
                                "description": STR, "qty": NUM, "unit": STR, "unit_price": NUM,
                                "amount": NUM, "hs_code": STR, "country_of_origin": STR},
                 # 🔴 고객 요구 항목(’26.09.30 메일)은 전부 필수로 둔다. 출력 토큰을 줄이려고
                 #    unit 과 country_of_origin 을 필수에서 뺐더니 모델이 통째로 생략했고
                 #    UNIT 이 44줄 중 0줄로 나왔다. 벽시계는 그렇게 해도 안 줄었다(26.3 → 26.7초).
                 "required": ["seq", "invoice_no", "sap_code", "po_no", "po_item_no",
                              "material_code", "serial_no", "description", "qty", "unit",
                              "unit_price", "amount", "country_of_origin"]}}
HYNIX_PL_LINE = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                 "properties": {"seq": NUM, "invoice_no": STR, "case_no": STR, **HYNIX_CODES,
                                "description": STR, "qty": NUM, "unit": STR,
                                "net": NUM, "gross": NUM},
                 "required": ["seq", "invoice_no", "sap_code", "po_no", "po_item_no",
                              "description", "qty", "net", "gross"]}}
# 건 단위로 더하는 칸 둘. 묶음에 인보이스가 여러 장이면 줄만으로는 어느 장의 합인지 모른다.
# 통관 용도설명서(중국 해관 신고 요소)의 품목 줄. 인보이스에 Material 열이 없는 서류에서
# 이 표가 유일한 SAP 코드 출처다. 열 이름이 중국어와 영어와 한국어로 세 줄 겹쳐 인쇄된다.
HYNIX_CN_LINE = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                 "properties": {"seq": NUM, "sap_code": STR, "desc_en": STR, "desc_cn": STR,
                                "model": STR, "country_of_origin": STR, "net_weight": NUM},
                 "required": ["seq", "sap_code", "desc_en"]}}
HYNIX_PROPS = {
    "cn_lines": HYNIX_CN_LINE,
    # 🔴 세액. 수리 인보이스는 「Total(Repair Price) 126,432.18 / VAT(13%) 16,436.21 /
    #    Total Amount 142,868.39」 꼴로 찍힌다. 줄 합은 세전 합계와 맞는데 총액은 세후라서
    #    규칙이 「합이 안 맞는다」 고 잡았다. 줄을 정확히 다 뽑았는데도 오류로 뜬다.
    "ci_tax": NUM,
    "po_list": {"type": "array", "items": STR},
    "invoice_totals": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                       "properties": {"invoice_no": STR, "total": NUM, "currency": STR},
                       "required": ["invoice_no", "total", "currency"]}},
}
SCHEMA_H1 = {**SCHEMA_V4,
             "properties": {**SCHEMA_V4["properties"], **HYNIX_PROPS,
                            "ci_lines": HYNIX_CI_LINE, "pl_lines": HYNIX_PL_LINE},
             "required": SCHEMA_V4["required"] + list(HYNIX_PROPS)}
SCHEMA_H1_STRICT = strict_schema(SCHEMA_H1)



def pipe_keys(sc=None):
    """스키마가 뽑는 키를 전부 편다. `schema/fields.json` 의 pipe_key 와 견주는 자리다.

    배열 안의 값은 `ci_lines[].qty` 꼴로 적는다. 배열 자체(`ci_lines`, `dims_cm`)도
    키로 같이 넣는다. 항목 하나가 배열 통째를 가리킬 때가 있다.
    """
    sc = SCHEMA_V4 if sc is None else sc
    out = set()

    def walk(node, prefix=""):
        for name, sub in (node.get("properties") or {}).items():
            path = prefix + name
            if sub.get("type") == "array":
                out.add(path)
                item = sub.get("items") or {}
                if item.get("properties"):
                    walk(item, path + "[].")
                else:
                    out.add(path + "[]")
            else:
                out.add(path)

    walk(sc)
    return out

SYSTEM = """You extract fields from air-freight shipping document bundles (air waybill / HAWB, commercial invoice, packing list, LCA charge sheet, DHL waybill).
Rules:
- Copy values exactly as printed. Do not compute, convert, or guess. If a field is not printed, use null (or an empty array).
- Numbers are plain numbers (12000 not "12,000"). Keep decimals as printed.
- If the same document (same invoice number or same AWB) appears twice in the bundle, read it once. Do not duplicate its lines or charges.
- If the bundle has several different invoices, include the lines of all of them in ci_lines.
- hawb: the house air waybill / HAWB / H/BL / DHL waybill number only (e.g. J283048, 2076679570). Do not include label words like WAYBILL.
- mawb: the master air waybill / MAWB / M/BL number, 11 digits usually printed as NNN-NNNNNNNN (e.g. 988-13444266). It is often printed at the top of the AWB next to the HAWB number without a label. Keep the printed form.
- invoice_no: the commercial invoice number. customer_po: the buyer's PO number (PO Number / Customer PO / P/O No.), the first one if several. item_desc: the main goods description; if it contains a double quote such as 3", write it as 3 inch.
- incoterms: the 3-letter Incoterm (EXW, FOB, DAP ...). currency: the invoice currency; if no currency label is printed, take it from the amount column header such as AMT.(USD).
- origin / destination: the airport or city NAME printed in the Airport of Departure / Airport of Destination boxes of the AWB (e.g. TAIPEI, DALIAN), not the 3-letter code. flight1: the first flight number only (OZ752), without date or routing.
- ci_lines: one entry per printed invoice line with qty, unit price, line amount. ci_total: the invoice total amount as printed.
- ci_gross / ci_net: gross / net weight in kg printed on the invoice. pl_* the same from the packing list, using the packing list's TOTAL line; pl_packages: total number of packages on the packing list.
- pl_lines: one entry per printed packing-list line. Do not merge or sum lines.
- awb_pieces: number of pieces on the AWB; awb_gross: AWB gross weight kg; awb_chargeable: AWB chargeable weight kg; awb_declared_value: "Declared Value for Customs" on the AWB or courier waybill, number only (6582.00 USD -> 6582.00), null if NCV / NVD / N.C.V.
- dims_cm: package dimensions in cm as printed, one entry per distinct size with count n.
- lca_lines: rows of the LCA / charges table. code is the 3-letter charge code only (FSC, not "FSC (AFS)"). issue_amt is the amount in the issue currency, exrate the exchange rate, usd_amt the USD amount.
- lca_total_issue: the "Total (<currency>)" line of the LCA table, i.e. the sum of issue_amt. Never the "Other" or "Logistic Amt" line. lca_grand_total_usd: the "Grand Total (USD)" line.
- package_unit: the package word as printed (CARTON, BOX, PALLET, PCS ...).
Return only the JSON object."""

PARTY_RULES = """
- shipper_name / shipper_address: the company name and the address printed in the Shipper's Name and Address box of the AWB. If the AWB has none, take the seller from the invoice. Copy the name on its own; put the rest of the box in the address as one line.
- consignee_name / consignee_address: the same from the Consignee's Name and Address box, or the buyer / Sold to on the invoice."""
SYSTEM_V3 = SYSTEM.replace("\nReturn only the JSON object.", PARTY_RULES + "\nReturn only the JSON object.")
SYSTEM_V2, SCHEMA_V2, SCHEMA_V2_STRICT = SYSTEM, SCHEMA, SCHEMA_STRICT

CHECK_RULES = """
- agent_iata_code: the Agent's IATA Code box, digits only as printed (e.g. 3430409/0004). Never the Account No.
- agent_account_no: the Account No. box. It is a different box from the IATA code; do not swap them.
- freight_term: PREPAID or COLLECT, whichever is marked on the AWB or B/L. null if neither is marked.
- awb_weight_charge / awb_valuation_charge / awb_tax: the amounts in those AWB boxes.
- awb_other_charges_agent / awb_other_charges_carrier: "Total other Charges Due Agent" / "Due Carrier".
- awb_total: "Total Prepaid" if freight_term is PREPAID, "Total Collect" if COLLECT.
- measurement_cbm: the total measurement in CBM printed on the B/L or packing list.
- pl_lines: add net and gross weight in kg for each printed line when the line shows them."""
SYSTEM_V4 = SYSTEM_V3.replace("\nReturn only the JSON object.", CHECK_RULES + "\nReturn only the JSON object.")


# v5: 칸은 v4 그대로 두고 규칙만 좁힌다. 잡음 실측(5건 82.9%)에서 회차마다 갈린 자리를
# 하나씩 짚어 그 칸이 어느 서류의 어느 상자에서 오는지 못 박는다. 같은 값을 두 자리에서
# 읽을 수 있으면 흔들린다. 자리를 하나로 정하면 흔들릴 여지가 없다.
STABLE_RULES = """
Where a value can be read from more than one place, the source is fixed as follows. Never take it from anywhere else.
- hawb: the house waybill number printed in the HAWB number box at the top of the house air waybill (the number that the forwarder assigned, often letters followed by digits). Remove spaces inside the number. Never a courier tracking number, an invoice number, a PO number, a bank reference or the MAWB.
- mawb: the 11-digit master number (3-digit airline prefix + 8 digits). It may be printed with a space or dash inside the 8 digits (933-2773 3031); write it as NNN-NNNNNNNN with all 11 digits.
- shipper_name / consignee_name: the Shipper box is the first party box at the top-left of the AWB, the Consignee box is the second box directly below it. The company next to "Issuing Carrier's Agent" is the agent and is never the shipper or consignee. On invoices, Seller / From = shipper, Sold to / Consignee / Ship to = consignee.
- origin / destination: copy the text of the Airport of Departure / Airport of Destination boxes exactly as printed (a name, or a 3-letter code if only a code is printed). Do not translate a code into a name or add a country that is not printed in that box.
- item_desc: the "Nature and Quantity of Goods" text of the AWB, first line only, without piece counts or dimensions. If the bundle has no AWB, the description of the first invoice line.
- ci_total: if the bundle has several invoices, the sum of their printed totals. invoice_no: the number of the first invoice.
- ci_gross / ci_net / pl_gross / pl_net / pl_packages: the TOTAL line of that document. If the document has no total line, the sum of its lines.
- pl_lines: one entry per printed packing-list line, in printed order. Do not merge lines with the same description.
- awb_pieces / awb_gross / awb_chargeable: from the AWB rate box (No. of Pieces, Gross Weight, Chargeable Weight). Never from the invoice or packing list.
- freight_term: only PREPAID or COLLECT. Take it from the marked PPD/COLL box or the words "FREIGHT COLLECT" / "FREIGHT PREPAID" on the AWB.
- currency: the 3-letter currency code printed on the invoice amounts (USD, JPY, KRW ...). If the invoice prints none, the AWB currency box.
- package_unit: the package word on the packing list total line or the AWB (CARTON, PALLET, BOX, PKG, SLAC ...). One word, singular, uppercase."""
SYSTEM_V5 = SYSTEM_V4.replace("\nReturn only the JSON object.", STABLE_RULES + "\nReturn only the JSON object.")

# v6: 프롬프트에서 보기 값을 전부 뺀다. 잡음 실측에서 모델이 칸을 못 찾으면 프롬프트의
# 보기(3430409/0004, J283048, 988-13444266)를 그대로 적었다. 세 케이스가 같은 IATA 코드를
# 냈고 셋 다 서류에 없는 값이었다. 꼴만 적고 값은 적지 않는다. 서류가 여럿이면 합으로 못 박는다.
SYSTEM_V6 = """You extract fields from an air-freight shipping document bundle: house air waybill (HAWB), commercial invoice (CI), packing list (PL), sometimes an LCA charge sheet or a courier waybill.
General rules:
- Copy values exactly as printed. Do not compute, convert, translate or guess. If a value is not printed anywhere in the bundle, use null (or an empty array). Never fill a field with a value from these instructions.
- Numbers are plain numbers without thousands separators. Keep decimals as printed.
- If the same document (same invoice number or same waybill number) appears twice, read it once.
- If the bundle has several different invoices or packing lists, ci_lines and pl_lines hold the lines of all of them, and every total (ci_total, ci_gross, ci_net, pl_gross, pl_net, pl_packages) is the sum of the printed totals of those documents. dims_cm lists the sizes of all of them.
Where each value comes from. Take it from that place only:
- hawb: the house waybill number in the HAWB number box at the top of the house air waybill, or the courier waybill number if the bundle has only a courier waybill. Copy it exactly as printed, including any letters and spaces inside it. Never an invoice number, PO number, tracking number, bank reference or the MAWB.
- mawb: the 11-digit master air waybill number: a 3-digit airline prefix and 8 digits. It may be printed with a space or dash inside the digits, or with a 3-letter airport code between the prefix and the digits (NNN XXX NNNN NNNN, NNN-XXX-NNNNNNNN); drop the letters and write it as NNN-NNNNNNNN with all 11 digits. It is often printed at the top of the AWB or in a box labelled MAWB / M:. When the top line prints two numbers, the one with the 3-digit airline prefix is the MAWB and the other one is the HAWB. null if not printed.
- invoice_no: the number of the first commercial invoice. customer_po: the buyer's purchase order number printed as PO / P/O / Customer PO / Order No, the first one.
- incoterms: the 3-letter Incoterm as printed. currency: the 3-letter currency code printed with the invoice amounts; if none is printed with the amounts, the currency in the column header. Never the AWB currency box: that is the currency of the freight charges, not of the goods.
- origin / destination: the text of the Airport of Departure / Airport of Destination boxes of the AWB exactly as printed. When the AWB prints both a full airport or city name (in the airport box) and a 3-letter code (in the routing line), take the name. Take the code only when no name is printed. Do not turn a code into a name or add a country that is not printed in that box. Without an AWB, the From / To or Port of Loading / Port of Discharge of the invoice or packing list.
- flight1: the first flight number only (airline code and digits), without date or routing. null if none is printed.
- item_desc: the goods description in the Nature and Quantity of Goods box of the AWB: its description lines joined with a space, without the piece-count prefix (such as "4 PKG OF"), PO or INV numbers, dimensions, "NOT DG" or "AS ARRANGED". Without an AWB, the description of the first invoice line. Write a double-quote inch sign as the word inch.
- shipper_name / shipper_address: the Shipper box of the AWB, which is the first party box at the top left, above the Consignee box. The company printed next to Issuing Carrier's Agent is the agent and is never the shipper or the consignee. Without an AWB, the Seller / From / Shipper of the invoice. Name alone in shipper_name; the rest of the box as one line in shipper_address.
- consignee_name / consignee_address: the Consignee box of the AWB, directly below the Shipper box. Without an AWB, the Consignee / Sold to / Ship to / Buyer of the invoice.
- agent_iata_code: the complete content of the Agent's IATA Code box of the AWB, exactly as printed. It is a 7-digit agent code with a check digit, often printed in the pattern NN-N NNNN/NNN N or NNNNNNN/NNNN; copy all of its digits together with the dash, space and slash. agent_account_no: the Account No. box next to it. They are different boxes; if a box is empty, null. Never copy one into the other.
- freight_term: PREPAID or COLLECT, from the marked PPD/COLL box or the words FREIGHT COLLECT / FREIGHT PREPAID on the AWB. Nothing else; null if neither is printed.
- awb_pieces / awb_gross / awb_chargeable: the No. of Pieces, Gross Weight and Chargeable Weight of the AWB rate box. On most forms this is one line printed in this order: number of pieces, gross weight, the letter K (kg), a rate class letter (Q, N, M ...), chargeable weight, then the rate or charge (often AS ARRANGED). Never from the invoice or the packing list.
- awb_declared_value: the number in the Declared Value for Customs box, without the currency or Incoterm prefix (the box often prints an Incoterm and a currency code glued to the amount, such as three Incoterm letters followed by USD or JPY or KRW and then the number; take only the number). The Declared Value for Carriage box next to it usually says N.V.D or NCV; that is a different box. null only when the Customs box itself says NCV, NVD, N.C.V. or N.V.D or is empty.
- awb_weight_charge / awb_valuation_charge / awb_tax / awb_other_charges_agent / awb_other_charges_carrier / awb_total: the amounts in those boxes of the AWB charges section; awb_total is Total Prepaid when freight_term is PREPAID and Total Collect when COLLECT. null when the box says AS ARRANGED or is empty.
- ci_lines: one entry per printed invoice line with qty, unit price and line amount as printed. ci_total: the printed invoice total (the sum over invoices when there are several). Repair invoices print two tables with the same line items: one with the original value of the parts and one with the repair price. Which one is labelled "Actual Repair Price" differs between vendors, so do not go by the label: use only the table whose line amounts add up to the printed Total (Repair Price) line, and ignore the other table entirely.
- ci_gross / ci_net: gross and net weight in kg printed on the invoice total line, null if the invoice prints none.
- pl_lines: one entry per printed packing-list line in printed order, with qty and, when the line prints them, net and gross weight in kg. Do not merge lines with the same description.
- When a packing list prints a GRAND TOTAL after a returned-container, pallet or cylinder section, pl_gross and pl_net are the GRAND TOTAL (it is what the bill of lading carries). When the PO box says "see below" or "see following", customer_po is the first PO number in the line table.
- pl_packages / pl_gross / pl_net: the TOTAL line of the packing list: number of packages (cartons, boxes, pallets, cases), gross kg, net kg. pl_packages is never the piece quantity (EA / PCS / SET); when the packing list prints only a piece quantity, take the package count from the C/T No, Total Packages or Packing details text. If the packing list has no total line, the sum of its lines.
- package_unit: the package word printed on the packing-list total line or the AWB: one word, singular, uppercase, such as the word for a carton, pallet, box, case, package or skid. null if none is printed.
- dims_cm: package dimensions in cm from the DIM text of the AWB; without an AWB, from the packing list. One entry per distinct size with the count n of packages of that size.
- measurement_cbm: the total volume in cubic metres printed as CBM or M3 on the AWB, waybill or packing list total line; null if not printed. Never a volume in litres or gallons.
- Sea bundles: a bill of lading (B/L) takes the place of the air waybill. A sea bundle often holds TWO bills of lading. The HOUSE B/L is issued by the forwarder: its shipper is the seller or manufacturer and its consignee the buyer (forms titled MULTIMODAL TRANSPORT BILL OF LADING, or a forwarder's own B/L form). The MASTER B/L is issued by the shipping line: its shipper is the forwarder (often written "X O/B OF Y"), its consignee a logistics agent, and it is often marked CHECK B/L or COPY NON-NEGOTIABLE or carries a carrier name (SINOKOR, SITC, WEIDONG, EVERGREEN, SINOTRANS, JINJIANG, LYG FERRY, a ferry company). Read shipper, consignee, origin, destination, item_desc, awb_pieces, awb_gross, measurement_cbm and freight_term from the HOUSE B/L only. hawb is the house B/L number. mawb is the master B/L number (some house B/Ls also print it as Document No. or M.B/L NO); null when no master number is printed. flight1: the vessel name and the voyage number separated by one space, without V., VOY or a slash. origin: the Port of Loading box; destination: the Port of Discharge box, never the Place of Delivery or Final Destination. item_desc: the goods description printed after SAID TO CONTAIN, without SHIPPER'S LOAD & COUNT and without the package-count prefix such as "8 PACKAGES OF". awb_pieces: the number of packages or pallets in the No. of Packages box of the house B/L (not the container count). On a house B/L form without box labels, the boxes run top to bottom: shipper, consignee, notify party, then the delivery agent (a logistics company, never the consignee); after the vessel and voyage line the first place is the Port of Loading and the next is the Port of Discharge. freight_term: FREIGHT COLLECT / FREIGHT PREPAID exactly as printed on the HOUSE B/L; the master B/L is usually PREPAID even when the house B/L says COLLECT. awb_chargeable null.
- lca_lines: the rows of the LCA charge table: code is the 3-letter charge code only, issue_amt the amount in the issue currency, exrate the exchange rate, usd_amt the USD amount. lca_total_issue: the Total line of that table in the issue currency; lca_grand_total_usd: the Grand Total (USD) line. All null / empty when the bundle has no LCA sheet.
Return only the JSON object."""

# h1: 하이닉스 중국법인 판. v6 의 규칙을 그대로 두고 줄 단위 대목만 얹는다. 건 단위 칸의
# 정의를 건드리지 않아야 우리 실측값과 이 판의 값을 같은 자로 견줄 수 있다.
HYNIX_RULES = """
Line-level rules. The buyer loads these lines into an ERP screen that links each shipped item to its purchase order, so a line is only usable when it carries its own order number and item codes.
- Read the column headers of the invoice and packing-list tables and copy each value from the column whose printed header is named below. Do not infer a field from the look of a value. When a table prints no column with that header, the field is null for every line of that table.
- Read every printed line of every table you use, in printed order, and do not merge lines that repeat the same description. But a repair invoice prints the SAME line items twice, once at the original value of the part and once at the repair price, and only one of the two tables is the invoice: use only the table whose line amounts add up to the printed Total (Repair Price) line and ignore the other table entirely. Two tables with the same items are never two shipments.
- ci_lines: one entry per printed commercial-invoice line.
  - invoice_no: the invoice number printed on the document this line belongs to. When the bundle holds several invoices, each line carries the number of its own invoice.
  - seq: the line number printed in the first column (No. / S/N) of that invoice table. It restarts at 1 in each invoice; keep it as printed.
  - sap_code: the column headed "Sap Code" or "SAP Code".
  - po_no: the column headed "Po No.", "PO No." or "Purchase Order".
  - po_item_no: the column headed "Po Item No." or "PO Item". It is usually a short zero-padded number such as 00010.
  - iv_no: the column headed "SK Hynix I/V No." or "I/V No.".
  - hios_no: the column headed "HIOS No.".
  - material_code: the column headed "Material", "Material No", "Material Code" or "Item Code", whatever else the table also prints. It is a different column from sap_code; a table often prints both, and each line then carries both values. Null only when no such column is printed.
  - serial_no: the column headed "Serial No", "SERIAL NUMBER" or "S/N". Null when that column is not printed.
  - part_no: the seller's part number column (Part No., Vendor Part No., Model, Article No.). Fill it only when none of the named columns above covers that value.
  - description, qty, unit, unit_price, amount: as printed on that line. qty is a bare number with no unit word; the unit word goes in unit, uppercase, such as EA, PCS or SET.
  - hs_code, country_of_origin: as printed on that line. Null when the invoice prints them once for the whole document rather than per line.
- pl_lines: one entry per printed packing-list line, with the same invoice_no, seq, code-column and description rules, plus:
  - case_no: the case, carton or C/T number printed on that line.
  - net, gross: the net and gross weight of that line in kg as printed.
- po_list: every distinct purchase order number printed anywhere in the bundle, in printed order, including any listed in the invoice header and in the goods description box of the waybill. Empty array when none is printed.
- invoice_totals: one entry per invoice in the bundle with its invoice number, its printed total amount and its currency. For a repair invoice the total is the Total (Repair Price) line.
- ci_tax: the tax or VAT amount printed on the invoice as a separate line (VAT, V.A.T, TAX, 부가세). null when the invoice prints none. ci_total stays the printed grand total.
- cn_lines: only for a Chinese customs declaration sheet (a wide table whose headers are printed in Chinese and English together, such as 物料代码 / SKhynix SAP Code, 英文描述 / Discription, 原产国 / Country Of Origin). One entry per printed item row: seq from the 序列号 / No. column, sap_code from the 物料代码 / SKhynix SAP Code column, desc_en from the English description column, desc_cn from the Chinese description column, model from the 规格型号 / Model column, country_of_origin from the 原产国 column, net_weight from the 净重 column in kg. Empty array for any other document.
  sap_code is the single most important value on that sheet. It is the second column, immediately right of the row number, headed 物料代码 on the top line and SKhynix SAP Code on the line below it. Its values are short codes of letters and digits such as one letter followed by six to eight characters. Read that column for every printed row even when the scan is faint, and never leave it empty while the row's description is filled. If a row's code is unreadable, copy what you can see rather than omitting it."""
SYSTEM_H1 = SYSTEM_V6.replace("\nReturn only the JSON object.", HYNIX_RULES + "\nReturn only the JSON object.")

PROFILES = {
    "v2": lambda: (SCHEMA_V2, SCHEMA_V2_STRICT, SYSTEM_V2),
    "v3": lambda: (SCHEMA_V3, SCHEMA_V3_STRICT, SYSTEM_V3),
    "v4": lambda: (SCHEMA_V4, SCHEMA_V4_STRICT, SYSTEM_V4),
    "v5": lambda: (SCHEMA_V4, SCHEMA_V4_STRICT, SYSTEM_V5),
    "v6": lambda: (SCHEMA_V4, SCHEMA_V4_STRICT, SYSTEM_V6),
    "h1": lambda: (SCHEMA_H1, SCHEMA_H1_STRICT, SYSTEM_H1),
}


# ---------- 3.5 칸의 뜻을 스키마에 적는다 ----------
# 🔴 왜 스키마에 적는가. 칸의 뜻은 지금까지 SYSTEM 프롬프트에만 있었다. 프롬프트를 받는
#    경로(클로드, 솔라)는 "flight1 은 첫 편명만, 날짜와 경유지 없이" 를 읽지만, 프롬프트를
#    받지 않는 경로(Upstage Information Extract)는 `flight1` 이라는 키 이름만 받았다.
#    키 이름만 보고 뜻을 알아낼 방법이 없다. 그래서 정보추출은 편명을 운송장이 든 네
#    묶음에서 넷 다 놓쳤고, 품명과 포장 단위에서도 밀렸다. 그 값을 제품 성적으로 표에
#    올리면 잘못 잰 값을 올리는 것이다.
#
#    정의를 스키마로 옮기면 세 경로가 같은 정의를 받는다. 프롬프트에도 남겨 두는데,
#    같은 말을 두 곳에 적는 비용보다 한 경로가 정의를 못 보는 비용이 크다.
#
# 🔴 설명을 켜고 끌 수 있게 둔다. 설명의 효과 자체가 재 볼 값이라서다. 조건으로 두면
#    "우리 스키마가 나빴다" 를 숫자로 말할 수 있고, 그것이 상대에게 쓸모 있는 발견이다.
FIELD_DOC = {
    "hawb": "House air waybill (HAWB / H/BL) or courier waybill number only, e.g. J283048 or 2076679570. Exclude label words such as WAYBILL.",
    "mawb": "Master air waybill (MAWB / M/BL) number, usually 11 digits printed as NNN-NNNNNNNN, e.g. 988-13444266. Often printed at the top of the air waybill next to the house number with no label. Keep the printed form.",
    "invoice_no": "Commercial invoice number as printed on the invoice.",
    "customer_po": "Buyer's purchase order number (PO Number / Customer PO / P/O No.). If several are printed, the first one.",
    "item_desc": "Main goods description. Write an inch mark as the word inch, so 3\" becomes 3 inch.",
    "incoterms": "Three-letter Incoterm printed on the invoice, such as EXW, FOB or DAP.",
    "currency": "Invoice currency. If no currency label is printed, take it from the amount column header such as AMT.(USD).",
    "origin": "Airport or city NAME printed in the Airport of Departure box of the air waybill, e.g. TAIPEI. Not the three-letter code.",
    "destination": "Airport or city NAME printed in the Airport of Destination box of the air waybill, e.g. DALIAN. Not the three-letter code.",
    "flight1": "First flight number only, e.g. OZ752. Exclude the date, the second leg and any routing.",
    "package_unit": "Packing unit word as printed, such as CARTON, PALLET or CASE.",
    "ci_lines": "One entry per printed commercial-invoice line: description, quantity, unit price, line amount. Do not merge or sum lines. If the bundle holds several different invoices, include the lines of all of them.",
    "ci_total": "Commercial invoice total amount as printed.",
    "ci_gross": "Gross weight in kg printed on the commercial invoice.",
    "ci_net": "Net weight in kg printed on the commercial invoice.",
    "pl_lines": "One entry per printed packing-list line: description and quantity. Do not merge or sum lines.",
    "pl_packages": "Total number of packages on the packing list TOTAL line.",
    "pl_gross": "Gross weight in kg from the packing list TOTAL line.",
    "pl_net": "Net weight in kg from the packing list TOTAL line.",
    "awb_pieces": "Number of pieces printed on the air waybill.",
    "awb_gross": "Gross weight in kg printed on the air waybill.",
    "awb_chargeable": "Chargeable weight in kg printed on the air waybill.",
    "awb_declared_value": "Declared Value for Customs on the air waybill or courier waybill, number only, so 6582.00 USD becomes 6582.00. Null when it reads NCV, NVD or N.C.V.",
    "dims_cm": "Package dimensions in cm as printed, one entry per distinct size with its count n.",
    "lca_lines": "Rows of the LCA charge table. code is the three-letter charge code only, so FSC rather than FSC (AFS). issue_amt is the amount in the issue currency, exrate the exchange rate, usd_amt the USD amount.",
    "lca_total_issue": "Total amount in the issue currency on the LCA charge sheet.",
    "lca_grand_total_usd": "Grand total in USD on the LCA charge sheet.",
    "shipper_name": "Shipper company name printed in the Shipper box.",
    "shipper_address": "Shipper address printed in the Shipper box.",
    "consignee_name": "Consignee company name printed in the Consignee box.",
    "consignee_address": "Consignee address printed in the Consignee box.",
    "freight_term": "Freight payment term as printed, PREPAID or COLLECT.",
    "agent_iata_code": "Agent's IATA code printed on the air waybill.",
    "agent_account_no": "Agent's account number printed on the air waybill.",
    "awb_weight_charge": "Weight charge amount printed on the air waybill.",
    "awb_valuation_charge": "Valuation charge amount printed on the air waybill.",
    "awb_tax": "Tax amount printed on the air waybill.",
    "awb_other_charges_agent": "Total other charges due agent printed on the air waybill.",
    "awb_other_charges_carrier": "Total other charges due carrier printed on the air waybill.",
    "awb_total": "Total printed on the air waybill.",
    "measurement_cbm": "Measurement in cubic metres as printed.",
    "transport_mode": "AIR or SEA.",
    "ci_tax": "Tax or VAT amount printed on the invoice as its own line. Null when none is printed.",
    "po_list": "Every distinct purchase order number printed anywhere in the bundle, in printed order. Empty array when none is printed.",
    "invoice_totals": "One entry per invoice in the bundle: its invoice number, its printed total amount and its currency. For a repair invoice the total is the Total (Repair Price) line.",
}

# ── 해상 판 칸 설명 ────────────────────────────────────────────────────
# 🔴 왜 갈라야 하나. FIELD_DOC 이 항공 기준으로 쓰여 있다. `flight1` 이 「첫 편명만, 예: OZ752」
#    이고 `origin` 이 「항공운송장의 출발 공항 상자」 다. 프롬프트를 받는 경로(소넷, 솔라,
#    모티프)는 SYSTEM_V6 의 해상 대목에서 해상 규칙을 읽으니 같은 칸을 96% 대로 맞힌다.
#    프롬프트를 받지 않는 경로(Upstage Information Extract)는 이 설명이 유일한 지시다.
#
#    ’26.09.22 실측에서 그 값이 드러났다. 해상 67건에서 편명 1.5%(대화 모델 96.2%),
#    운송장 번호 50.7%(96.6%) 다. 나머지 일곱 칸에서는 해상에서도 앞선다. 두 칸이 격차의
#    전부이고, 원인이 제품이 아니라 이 설명이다.
#
#    문구는 SYSTEM_V6 의 해상 대목에서 옮겨 적었다. 같은 말을 두 곳에 두는 비용보다
#    한 경로가 정의를 못 보는 비용이 크다(3.5절과 같은 판단).
FIELD_DOC_SEA = {
    "hawb": "House bill of lading (H/BL) number from the HOUSE B/L, the one issued by the forwarder. Exclude label words such as B/L NO.",
    "mawb": "Master bill of lading (M/BL) number, issued by the shipping line. Some house B/Ls print it as Document No. or M.B/L NO. null when no master number is printed.",
    "flight1": "Vessel name and voyage number separated by one space, e.g. SKY ORION 2451S. Drop V., VOY and any slash.",
    "origin": "Port name printed in the Port of Loading box of the house B/L, e.g. SHANGHAI. Not a code.",
    "destination": "Port name printed in the Port of Discharge box of the house B/L, e.g. INCHEON. Never the Place of Delivery or the Final Destination.",
    "item_desc": "Goods description printed after SAID TO CONTAIN on the house B/L. Drop SHIPPER'S LOAD & COUNT and drop a package-count prefix such as \"8 PACKAGES OF\".",
    "awb_pieces": "Number of packages or pallets in the No. of Packages box of the house B/L. Not the container count.",
    "awb_gross": "Gross weight in kg printed on the house B/L.",
    "measurement_cbm": "Measurement in cubic metres printed on the house B/L.",
    "freight_term": "FREIGHT COLLECT or FREIGHT PREPAID exactly as printed on the HOUSE B/L. The master B/L is usually PREPAID even when the house B/L says COLLECT.",
    "awb_chargeable": "Always null on a sea bundle. A bill of lading carries no chargeable weight.",
}

# ── 하이닉스 판의 줄 항목 설명 ──────────────────────────────────────
# 🔴 왜 따로 두나. `_with_desc` 는 맨 위 칸에만 설명을 붙인다. 하이닉스 판에서 값이 사는
#    자리는 줄 안쪽이라 거기에 설명이 없으면 프롬프트를 안 받는 경로가 키 이름만 받는다.
#    `po_no` 와 `part_no` 와 `material_code` 는 이름만 보고 가를 수 없다. 셋 다 서류에서
#    비슷하게 생긴 코드이고, 어느 열에서 오는지가 전부다.
#    기존 판(v2~v6)에는 안 붙인다. 붙이면 그때 잰 값과 지금 값이 다른 자로 잰 것이 된다.
FIELD_DOC_LINE_H1 = {
    "ci_lines": {
        "invoice_no": "Invoice number printed on the document this line belongs to.",
        "seq": "Line number printed in the first column (No. / S/N) of that invoice table, as printed. It restarts at 1 in each invoice.",
        "sap_code": "Value in the column headed Sap Code or SAP Code. Null when that column is not printed.",
        "po_no": "Value in the column headed Po No., PO No. or Purchase Order. Null when that column is not printed.",
        "po_item_no": "Value in the column headed Po Item No. or PO Item, usually a short zero-padded number such as 00010. Null when that column is not printed.",
        "iv_no": "Value in the column headed SK Hynix I/V No. or I/V No. Null when that column is not printed.",
        "hios_no": "Value in the column headed HIOS No. Null when that column is not printed.",
        "material_code": "Value in the column headed Material, Material No, Material Code or Item Code. A different column from Sap Code; a table often prints both.",
        "serial_no": "Value in the column headed Serial No, SERIAL NUMBER or S/N.",
        "part_no": "Seller's part number column (Part No., Vendor Part No., Model, Article No.).",
        "description": "Goods description printed on this line.",
        "qty": "Quantity printed on this line, as a bare number without the unit word.",
        "unit": "Quantity unit word printed on this line, uppercase, such as EA, PCS or SET.",
        "unit_price": "Unit price printed on this line.",
        "amount": "Line amount printed on this line.",
        "hs_code": "HS or tariff code printed on this line. Null when the invoice prints one for the whole document.",
        "country_of_origin": "Country of origin printed on this line. Null when the invoice prints one for the whole document.",
    },
    "pl_lines": {
        "invoice_no": "Invoice number printed on the packing list this line belongs to.",
        "seq": "Line number printed in the first column of that packing-list table, as printed.",
        "case_no": "Case, carton or C/T number printed on this line.",
        "sap_code": "Value in the column headed Sap Code or SAP Code.",
        "po_no": "Value in the column headed Po No., PO No. or Purchase Order.",
        "po_item_no": "Value in the column headed Po Item No. or PO Item.",
        "iv_no": "Value in the column headed SK Hynix I/V No. or I/V No.",
        "hios_no": "Value in the column headed HIOS No.",
        "material_code": "Value in the column headed Material, Material No, Material Code or Item Code. A different column from Sap Code; a table often prints both.",
        "serial_no": "Value in the column headed Serial No, SERIAL NUMBER or S/N.",
        "part_no": "Seller's part number column (Part No., Vendor Part No., Model, Article No.).",
        "description": "Goods description printed on this line.",
        "qty": "Quantity printed on this line, as a bare number without the unit word.",
        "unit": "Quantity unit word printed on this line, uppercase.",
        "net": "Net weight of this line in kg as printed.",
        "gross": "Gross weight of this line in kg as printed.",
    },
}

SCHEMA_DESC = True        # 기본으로 켠다. 조건으로 끄려면 set_schema_desc(False)
LEG = None                # None 은 구간을 안 정한 것이다. set_leg("sea") 로 해상 판을 켠다
SCHEMA_VERSION = "s2"     # s1: 설명 없음(’26.09.10). s2: 항공 기준 설명. s3: 구간별 설명
# 🔴 스키마를 바꾸면 이 값을 올린다. 판이 다른 값끼리 견주면 안 되고, 산출물의 manifest 에
#    이 값이 함께 적혀서 나중에 섞이는 것을 막는다.


def field_doc(leg=None):
    """이 구간에서 쓸 칸 설명. 해상이면 해상 문구로 덮는다."""
    if (leg or LEG) == "sea":
        return {**FIELD_DOC, **FIELD_DOC_SEA}
    return dict(FIELD_DOC)


def _with_desc(sc, line_doc=None):
    """맨 위 property 에 칸 설명을 붙인 사본. line_doc 를 주면 그 배열의 줄 항목에도 붙인다."""
    if not isinstance(sc, dict) or "properties" not in sc:
        return sc
    doc = field_doc()
    props = {}
    for k, v in sc["properties"].items():
        d = doc.get(k)
        v = {**v, "description": d} if d and isinstance(v, dict) else v
        ld = (line_doc or {}).get(k)
        if ld and isinstance(v, dict) and isinstance(v.get("items"), dict) and "properties" in v["items"]:
            ip = {ik: ({**iv, "description": ld[ik]} if ik in ld and isinstance(iv, dict) else iv)
                  for ik, iv in v["items"]["properties"].items()}
            v = {**v, "items": {**v["items"], "properties": ip}}
        props[k] = v
    return {**sc, "properties": props}


def set_prompt(version="v3"):
    """프롬프트와 스키마를 통째로 바꾼다. 호출하는 자리는 그대로 두고 이 셋만 갈아 끼운다."""
    global PROMPT_VERSION, SCHEMA, SCHEMA_STRICT, SYSTEM
    if version not in PROFILES: raise ValueError(version)
    PROMPT_VERSION = version
    SCHEMA, SCHEMA_STRICT, SYSTEM = PROFILES[version]()
    if SCHEMA_DESC:
        ld = FIELD_DOC_LINE_H1 if version == "h1" else None
        SCHEMA, SCHEMA_STRICT = _with_desc(SCHEMA, ld), _with_desc(SCHEMA_STRICT, ld)
    # 줄 단위 표를 읽는 판은 표의 열이 남아 있는 글을 받아야 한다
    TEXT_FIX["table_sort"] = (version == "h1")


def _stamp():
    """조건 도장에 찍을 스키마 판. 설명을 끄면 s1, 구간을 정하면 s3, 아니면 s2 다."""
    if not SCHEMA_DESC:
        return "s1"
    return "s3" if LEG else "s2"


def set_schema_desc(on=True):
    """칸 설명을 켜거나 끈다. 끄면 ’26.09.10 벤치마크와 같은 스키마가 된다."""
    global SCHEMA_DESC, SCHEMA_VERSION
    SCHEMA_DESC = bool(on)
    SCHEMA_VERSION = _stamp()
    set_prompt(PROMPT_VERSION)


def set_leg(leg=None):
    """구간을 정한다. "sea" 면 해상 판 칸 설명을 쓰고 도장이 s3 가 된다.

    🔴 항공도 set_leg("air") 로 돌려야 한다. 그러면 도장이 똑같이 s3 이고 값이 s2 와 같아야
       한다. 항공이 안 변한 것을 보여야 「해상만 고쳤다」 를 말할 수 있다.
    """
    global LEG, SCHEMA_VERSION
    if leg not in (None, "air", "sea"):
        raise ValueError(leg)
    LEG = leg
    SCHEMA_VERSION = _stamp()
    set_prompt(PROMPT_VERSION)

set_prompt(PROMPT_VERSION)   # 기본은 v3


def build_messages(pages, vision=False):
    """쪽 머리글에 파일 이름을 안 쓴다.

    앞 판은 basename 을 그대로 넣었다. 케이스 폴더와 파일 이름에는 케이스 번호와
    고객 이름이 흔히 붙어 있어서, 모델이 서류를 안 읽고 거기서 값을 베낄 수 있다.
    CLI 쪽에서 실제로 그랬다. HAWB 가 폴더 이름으로 나왔고 원문에는 없었다.
    같은 구멍이 API 쪽 프롬프트에도 있었다. 파일을 1, 2 로 번호만 매긴다.
    """
    parts, content = [], []
    seq = {}
    for p in pages:
        seq.setdefault(p.file, len(seq) + 1)
        tag = f"doc{seq[p.file]}"
        if getattr(p, "needs_image", p.kind == "SCAN"):
            if vision:
                w, h, png = render_scan(p, dpi=110)
                content.append({"type": "text", "text": f"=== page {p.no} of {tag} (scanned image) ==="})
                content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}})
            else:
                ocr = (getattr(p, "ocr", "") or "").strip()
                parts.append(f"=== page {p.no} of {tag} ({p.kind}) ===\n[scanned page, no text layer]"
                             + (f"\n[machine OCR of this page for orientation only. It can confuse O/0, I/1, S/5, B/8 and drop digits. "
                                f"Take every value from the image; use this text only to locate it]\n{ocr[:3500]}" if ocr else ""))
            continue
        parts.append(f"=== page {p.no} of {tag} ({p.kind}) ===\n{p.text.strip()}")
    text = "\n\n".join(parts)
    if content:
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": [{"type": "text", "text": text}] + content}]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]

# 우리 도구의 이름을 대는 값이다. urllib 은 아무것도 안 보내면 스스로 Python-urllib/3.9 를
# 붙이는데, 그 이름을 받아 주지 않는 공급자가 있다(Infron 에서 403). 실제 부르는 쪽이
# 누구인지 그대로 적는 편이 상대 쪽 기록에도 낫다.
# HTTP 머리글은 latin-1 로만 보낼 수 있다. 한글을 넣으면 보내는 자리에서 멈춘다.
USER_AGENT = "docpipe/1.0 (fsk-ops document extraction)"


# 한도에 걸렸을 때 기다렸다 다시 부른다. 안 기다리면 그 케이스가 통째로 실패로 남는데,
# 실패한 케이스는 채점에서 빠지므로 표의 분모가 조용히 줄어든다. 솔라를 처음 돌렸을 때
# 항공 397건 가운데 325건이 그렇게 빠졌고, 남은 62건으로 낸 73.3% 가 전체 성적처럼 적혔다.
RETRY_STATUS = (429, 500, 502, 503, 504, 529)
RETRY_MAX = 6
RETRY_BASE = 4.0


def _retry_wait(err, attempt):
    """다음에 다시 부르기까지 기다릴 초. 서버가 Retry-After 를 주면 그것을 따른다."""
    after = None
    try: after = (err.headers or {}).get("Retry-After")
    except Exception: pass
    if after:
        try: return min(float(after), 120.0)
        except ValueError: pass
    return min(RETRY_BASE * (2 ** attempt), 120.0) + random.uniform(0, 3)


def _send(url, body, headers, timeout, reader):
    """한도와 일시적 오류에만 다시 부른다. 400 처럼 우리가 고쳐야 하는 응답은 그대로 올린다."""
    req_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers}
    for attempt in range(RETRY_MAX + 1):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=req_headers)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return reader(r), time.perf_counter() - t0
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == RETRY_MAX: raise
            time.sleep(_retry_wait(e, attempt))
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == RETRY_MAX: raise
            time.sleep(_retry_wait(e, attempt))


def _post(url, body, headers, timeout):
    return _send(url, body, headers, timeout, json.load)

# 한 호출이 낼 수 있는 출력 토큰. 생각을 하는 모델은 이 몫을 생각으로 먼저 쓰고 JSON 이
# 중간에서 끊긴다. 모티프 Motif 3 가 1,500 에서 그랬다. 모델마다 다르게 줄 수 있게 밖으로 뺀다.
MAX_OUTPUT_TOKENS = 1500


def set_max_output(n):
    global MAX_OUTPUT_TOKENS
    MAX_OUTPUT_TOKENS = int(n)


# 한 번에 받을 것인가 흘려 받을 것인가. 답을 다 만든 뒤에 보내는 서버는 앞단이 먼저 끊는다.
# 모티프 Motif 3 는 생각에만 105초를 써서 Cloudflare 가 524 로 끊었다. 흘려 받으면 첫 조각이
# 일찍 오므로 그 시계가 돌지 않는다. 답과 토큰 수는 같다.
STREAM = False

# 한 호출을 기다릴 최대 초. 앞 판은 30분이었다. 업스테이지가 한도를 넘긴 연결을 끊지 않고
# 붙들고만 있어서, 그 30분 동안 일꾼 셋이 아무것도 못 하고 서 있었다(실측: 25분간 케이스 0건).
# 짧게 끊고 다시 부르는 편이 빠르다. 흘려 받을 때는 첫 조각까지가 길 수 있어 넉넉히 둔다.
HTTP_TIMEOUT = None


def http_timeout():
    return HTTP_TIMEOUT or (1200 if STREAM else 180)


def set_http_timeout(sec):
    global HTTP_TIMEOUT
    HTTP_TIMEOUT = int(sec) if sec else None


def set_stream(on=True):
    global STREAM
    STREAM = bool(on)


def loads_partial(txt):
    """끊긴 JSON 을 끊긴 데까지 살린다. (값, 살렸는가) 를 돌려준다.

    솔라가 포장명세서의 같은 줄을 되풀이하다 목록 한가운데서 멈추고는 정상 종료라고
    적어 보낸 일이 있다(실측: 항공 150건 가운데 20건. 출력 143토큰, finish_reason=stop).
    같은 조건으로 세 번 불러도 같은 자리에서 끊겼고 temperature 를 올려도 같았다.
    통째로 버리면 그 케이스가 채점에서 빠져 분모가 조용히 줄고, 성적이 남은 케이스의
    것으로 적힌다. 끝난 원소까지만 받고 괄호를 닫아 쓴다. 덜 받은 것은 빈 값이 된다.
    """
    try:
        return json.loads(txt), False
    except json.JSONDecodeError:
        pass
    stack, in_str, esc, marks = [], False, False, []
    for i, ch in enumerate(txt):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch in "{[": stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack: stack.pop()
            if stack: marks.append((i, list(stack)))
    for i, open_at in reversed(marks):
        try:
            return json.loads(txt[:i + 1] + "".join(reversed(open_at))), True
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("살릴 수 있는 원소가 없습니다", txt, 0)


def _read_sse(r):
    """SSE 조각을 이어 붙여 한 번에 받은 것과 같은 꼴로 만든다."""
    parts, usage, finish = [], {}, None
    for line in r:
        line = line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"): continue
        payload = line[5:].strip()
        if payload == "[DONE]": break
        try: ev = json.loads(payload)
        except json.JSONDecodeError: continue
        if ev.get("usage"): usage = ev["usage"]
        for ch in ev.get("choices") or []:
            if ch.get("finish_reason"): finish = ch["finish_reason"]
            piece = (ch.get("delta") or {}).get("content")
            if piece: parts.append(piece)
    return {"choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}], "usage": usage}


def _post_stream(url, body, headers, timeout):
    return _send(url, body, headers, timeout, _read_sse)


def call_openai_compat(base, model, messages, timeout=None, max_tokens=None, key=None, schema=None):
    """llama-server / Ollama / vLLM / Upstage Solar 처럼 OpenAI 호환 서버. JSON 스키마를 주면 llama-server 는 문법으로 강제한다.

    schema 를 주면 그것을 강제한다. 서류별로 나눠 부를 때 그 서류의 칸만 남긴 부분
    스키마가 들어온다. 앞 판은 전체 스키마를 박아 두어서, 나눠 부르기로 정답 대조를
    78.5% 에서 98.1% 로 올린 얼개를 솔라 쪽에서는 쓸 수가 없었다. 자를 다르게 대고
    잰 값은 견줄 수가 없다.
    """
    max_tokens = max_tokens or MAX_OUTPUT_TOKENS
    timeout = timeout or http_timeout()
    local = "127.0.0.1" in base or "localhost" in base
    sc = schema or (SCHEMA if local else SCHEMA_STRICT)
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "extract", "schema": sc}}}
    if local: body["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Authorization": "Bearer " + key} if key else {}
    if STREAM: body["stream"] = True
    post = _post_stream if STREAM else _post
    try:
        out, wall = post(base.rstrip("/") + "/chat/completions", body, headers, timeout)
    except urllib.error.HTTPError as e:
        if e.code != 400: raise
        # json_schema 를 안 받는 서버면 json_object 로 물러서고 스키마는 프롬프트에 넣는다
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [dict(messages[0], content=messages[0]["content"] + "\nJSON schema:\n" + json.dumps(sc))] + messages[1:]
        out, wall = post(base.rstrip("/") + "/chat/completions", body, headers, timeout)
    txt = out["choices"][0]["message"]["content"]
    usage = out.get("usage", {}); tm = out.get("timings", {})
    value, salvaged = loads_partial(txt)
    return value, dict(wall=wall, prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                       prompt_tps=tm.get("prompt_per_second"), gen_tps=tm.get("predicted_per_second"),
                       salvaged=salvaged or None)

def call_upstage_ie(what, timeout=600, schema=None):
    """Upstage Information Extract. 보낼 쪽을 PDF 하나로 묶어 스키마와 함께 보낸다.

    what 은 Page 목록이거나 파일 경로 목록이다. **Page 목록을 주는 편을 쓴다.**

    🔴 앞 판은 파일 경로를 받아 묶음의 모든 쪽을 보냈다. 다른 경로는 select_pages 가
       고른 쪽만 받는데 이 경로만 약관과 내부 메모까지 받았다. J283048 은 클로드가
       네 쪽, 정보추출이 아홉 쪽이었다. 쪽당 과금이라 돈도 더 냈다. 고른 쪽만 보내
       같은 조건으로 만든다.

    🔴 스키마를 인자로 받는다. 서류별로 나눠 부를 때 그 서류의 칸만 넘긴다. 앞 판은
       SCHEMA_STRICT 를 박아 두어 마흔 칸을 한 번에 물렸다.
    """
    key = os.environ.get("UPSTAGE_API_KEY")
    if not key: raise SystemExit("UPSTAGE_API_KEY 가 없다")
    doc = pymupdf.open()
    if what and isinstance(what[0], str):
        for p in what: doc.insert_pdf(pymupdf.open(p))
    else:
        for p in what: doc.insert_pdf(pymupdf.open(p.file), from_page=p.no - 1, to_page=p.no - 1)
    n_pages = len(doc); pdf = base64.b64encode(doc.tobytes()).decode()
    body = {"model": "information-extract",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:application/pdf;base64," + pdf}}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "extract", "schema": schema or SCHEMA_STRICT}}}
    out, wall = _post("https://api.upstage.ai/v1/information-extraction", body, {"Authorization": "Bearer " + key}, timeout)
    u = out.get("usage", {}) or {}
    return json.loads(out["choices"][0]["message"]["content"]), dict(wall=wall, pages=n_pages, prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"))

def call_anthropic(model, messages, timeout=300, max_tokens=None, schema=None):
    """Anthropic Messages API. 스키마는 도구 정의로 주고 그 도구를 강제한다.

    스키마를 인자로 받는다. 앞 판은 SCHEMA 를 박아 두어서 서류별로 나눠 부를 때 쓸
    수가 없었다. 그래서 98.1% 를 낸 나눠 부르기가 CLI 로만 돌았고, 정작 운영에서 쓸
    API 경로는 한 번에 부르는 옛 얼개에 묶여 있었다.
    """
    max_tokens = max_tokens or MAX_OUTPUT_TOKENS
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: raise SystemExit("ANTHROPIC_API_KEY 가 없다")
    sys_txt = messages[0]["content"]; user = messages[1]["content"]
    if isinstance(user, list):   # 이미지 블록을 Anthropic 꼴로 바꾼다. 빈 텍스트 블록은 API 가 거부한다
        blocks = []
        for c in user:
            if c["type"] == "text":
                if c["text"].strip(): blocks.append({"type": "text", "text": c["text"]})
            else: blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": c["image_url"]["url"].split(",", 1)[1]}})
        user = blocks
    body = {"model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": sys_txt, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "extract", "description": "Extracted fields", "input_schema": schema or SCHEMA}],
            "tool_choice": {"type": "tool", "name": "extract"}}
    if "-4-" in model or model.endswith("-4"): body["temperature"] = 0   # Claude 5 부터는 temperature 를 받지 않는다
    out, wall = _post("https://api.anthropic.com/v1/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
    tool = next(b for b in out["content"] if b["type"] == "tool_use")
    u = out.get("usage", {})
    return tool["input"], dict(wall=wall, prompt_tokens=u.get("input_tokens"), completion_tokens=u.get("output_tokens"),
                               cache_read=u.get("cache_read_input_tokens"), cache_write=u.get("cache_creation_input_tokens"),
                               used_tool=True, usd=api_cost(model, u))


def api_cost(model, usage):
    """API 가 돌려준 usage 로 실비를 낸다. 캐시 쓰기는 입력의 1.25배, 캐시 읽기는 0.1배다."""
    key = next((k for k in PRICE if k in (model or "")), None)
    if not key: return None
    pi, po = PRICE[key]
    return ((usage.get("input_tokens") or 0) * pi
            + (usage.get("cache_creation_input_tokens") or 0) * pi * 1.25
            + (usage.get("cache_read_input_tokens") or 0) * pi * 0.1
            + (usage.get("output_tokens") or 0) * po) / 1e6

def usr_text(messages):
    """build_messages 가 만든 사용자 쪽 글만 꺼낸다. 그림이 섞여 있으면 글 부분만 쓴다."""
    if len(messages) < 2:
        return ""
    u = messages[1]["content"]
    if isinstance(u, str):
        return u
    return "\n".join(c.get("text", "") for c in u if c.get("type") == "text")


# ---------- 2.5 스캔 쪽 가르기 (맥 Vision OCR) ----------
# 스캔 꾸러미를 통째로 한 번에 보내면 모델이 전체 스키마를 채우느라 오래 걸리고, 하우스와 마스터 B/L 을
# 뒤바꾸기도 했다. 모델을 부르기 전에 쪽마다 글자를 읽어(8쪽 3초) 종류를 가른다. 통관표, 약관, 위험물
# 검사증은 버리고, B/L 과 인보이스와 포장명세서는 그림 그대로 서류별 호출로 보낸다. 값은 여전히 모델이
# 그림에서 읽는다. OCR 글은 종류 가르기와 하우스, 마스터 역할 판정에만 쓴다.
import wuxi_paths  # 이관(10/29): 경로는 wuxi_paths 한 곳에서 정한다
OCR_BIN = os.environ.get("DOCPIPE_OCR") or os.path.join(wuxi_paths.BASE, "bin", "ocr_vision")
OCR_ROUTE = os.environ.get("DOCPIPE_OCR_ROUTE", "1") != "0"
OCR_DPI = 110
# OCR 엔진. vision 은 맥 전용(Apple Vision), rapid 는 리눅스 서버에서도 도는 RapidOCR(ONNX, CPU), none 은 끈다.
# 비워 두면 맥이고 Vision 실행 파일이 있을 때 vision, 아니면 rapid 를 쓸 수 있을 때 rapid, 둘 다 없으면 none.
# 비용을 잴 때 쓴다. 켜면 CLI 가 결과 사건을 낼 때까지 기다려서 토큰과 실비를 그대로 받는다.
# 평소에는 끈다. out.json 이 생기자마자 끊는 편이 케이스당 몇 초 빠르다.
WAIT_RESULT = os.environ.get("DOCPIPE_WAIT_RESULT", "") == "1"
OCR_ENGINE = os.environ.get("DOCPIPE_OCR_ENGINE", "").strip().lower()
# rapid 는 한 쪽에 1~3초가 든다. 쪽 종류와 B/L 번호는 윗부분에 찍히므로 위쪽만 읽어 시간을 줄일 수 있다(1.0 이면 전체).
OCR_CROP = float(os.environ.get("DOCPIPE_OCR_CROP", "1.0"))
# 🔴 일꾼 수와 일꾼마다 스레드 수. 예전에는 코어 절반만큼 일꾼을 띄우고 일꾼마다 스레드 하나였다(16코어면 8x1).
#    한 건의 스캔 쪽은 대개 1~4쪽이라 일꾼 8개 중 넷은 놀고, 한 쪽을 스레드 하나로 읽어 4쪽에 25~28초가 들었다.
#    일꾼 4개에 스레드 3개씩이면 4쪽이 13~14초, 16쪽은 그대로 50초 안팎이다(11/15, i5-1340P 노트북에서 번갈아 잼).
_CPU = os.cpu_count() or 2
OCR_WORKERS = int(os.environ.get("DOCPIPE_OCR_WORKERS", str(max(1, min(8, _CPU // 4)))))
OCR_THREADS = int(os.environ.get("DOCPIPE_OCR_THREADS", str(max(1, min(4, (_CPU * 3 // 4) // OCR_WORKERS)))))
_RAPID_POOL = None


def _ocr_text(lines):
    """Vision 줄 목록([글, x, y, w, h])을 위에서 아래, 왼쪽에서 오른쪽으로 잇는다. 같은 높이의 조각은 한 줄로 둔다."""
    rows = []
    for t, x, y, w, h in sorted(lines, key=lambda l: (l[2], l[1])):
        cy = y + h / 2
        if rows and abs(rows[-1][0] - cy) < max(0.006, h * 0.5):
            rows[-1][1].append((x, t))
        else:
            rows.append([cy, [(x, t)]])
    return "\n".join("   ".join(t for _, t in sorted(r[1])) for r in rows)


def ocr_engine():
    if OCR_ENGINE in ("vision", "rapid", "none"):
        return OCR_ENGINE
    if sys.platform == "darwin" and os.path.exists(OCR_BIN):
        return "vision"
    for mod in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            __import__(mod)
            return "rapid"
        except Exception:
            pass
    return "none"


_rapid_eng = None
_rapid_v3 = False


def no_power_throttle() -> bool:
    """Windows 가 이 프로세스를 「효율 모드」로 늦추지 않게 한다(배터리로 도는 노트북에서 OCR 이 서너 배 느려짐, 11/15).
    시스템 설정이 아니라 이 프로세스에만 거는 표시다. DOCPIPE_NO_THROTTLE=0 이면 안 건다."""
    if sys.platform != "win32" or os.environ.get("DOCPIPE_NO_THROTTLE", "1") == "0":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _PPT(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        st = _PPT(1, 0x1, 0)            # EXECUTION_SPEED 를 다루되 끈다
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetProcessInformation.restype = wintypes.BOOL
        return bool(k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)))  # ProcessPowerThrottling
    except Exception:
        return False


def _rapid_init():
    """RapidOCR 를 띄운다. 파이썬 3.12 까지는 rapidocr_onnxruntime(1.x), 3.13 부터는 rapidocr(3.x) 를 쓴다.

    두 판은 부르는 법과 돌려주는 꼴이 다르다. 1.x 는 ([상자, 글, 점수], 시간), 3.x 는 boxes, txts, scores 를 가진 객체다.
    """
    global _rapid_eng, _rapid_v3
    no_power_throttle()
    os.environ["OMP_NUM_THREADS"] = str(OCR_THREADS)
    try:
        from rapidocr_onnxruntime import RapidOCR
        _rapid_eng = RapidOCR(intra_op_num_threads=OCR_THREADS, inter_op_num_threads=1)
        _rapid_v3 = False
    except ImportError:
        import logging
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _rapid_eng = RapidOCR(params={"Global.use_cls": False, "Global.log_level": "warning",
                                      "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
                                      "EngineConfig.onnxruntime.inter_op_num_threads": 1})
        _rapid_v3 = True


def _rapid_one(args):
    """프로세스 하나에서 그림 한 장을 읽는다. 줄마다 [글, x, y, w, h] (0~1 비율)."""
    fn, crop = args
    import numpy as np
    from PIL import Image
    im = Image.open(fn).convert("RGB")
    w, h = im.size
    if crop < 1.0:
        im = im.crop((0, 0, w, int(h * crop)))
    if _rapid_v3:
        out = _rapid_eng(np.array(im))
        res = list(zip(out.boxes, out.txts, out.scores)) if out.boxes is not None else []
    else:
        res, _ = _rapid_eng(np.array(im), use_cls=False)
    lines = []
    for box, text, _score in (res or []):
        xs = [pt[0] for pt in box]; ys = [pt[1] for pt in box]
        lines.append([text, min(xs) / w, min(ys) / h, (max(xs) - min(xs)) / w, (max(ys) - min(ys)) / h])
    return lines


def _rapid_pool():
    global _RAPID_POOL
    if _RAPID_POOL is None:
        from concurrent.futures import ProcessPoolExecutor
        _RAPID_POOL = ProcessPoolExecutor(max_workers=OCR_WORKERS, initializer=_rapid_init)
    return _RAPID_POOL


def ocr_pages(pages):
    """스캔 쪽을 PNG 로 그려 page.png 에 두고 OCR 로 읽어 page.ocr 에 둔다. 돌려주는 것은 지울 임시 폴더다."""
    todo = [p for p in pages if p.kind == "SCAN"]
    if not todo or not OCR_ROUTE:
        return None
    d = tempfile.mkdtemp(prefix="docpipe-scan-")
    files = []
    for i, p in enumerate(todo):
        _, _, png = render_scan(p, dpi=OCR_DPI)
        fn = os.path.join(d, "p%03d.png" % i)
        with open(fn, "wb") as f:
            f.write(png)
        p.png = fn; files.append(fn)
    eng = ocr_engine()
    try:
        if eng == "vision":
            proc = subprocess.run([OCR_BIN] + files, capture_output=True, text=True, timeout=120)
            got = {}
            for line in proc.stdout.splitlines():
                try:
                    j = json.loads(line); got[j.get("file")] = j
                except json.JSONDecodeError:
                    pass
            for p, fn in zip(todo, files):
                p.ocr = fix_text(_ocr_text((got.get(fn) or {}).get("lines") or []))
                p.ocr_engine = eng
        elif eng == "rapid":
            results = list(_rapid_pool().map(_rapid_one, [(fn, OCR_CROP) for fn in files]))
            for p, lines in zip(todo, results):
                p.ocr = fix_text(_ocr_text(lines))
                p.ocr_engine = eng
    except Exception as e:
        sys.stderr.write(f"OCR 을 건너뜁니다: {str(e)[:80]}\n")
    return d


def route_scans(pages):
    """OCR 글로 스캔 쪽의 종류를 가른다. 글이 거의 없는 쪽(사진)은 그대로 SCAN 으로 둔다."""
    for p in pages:
        t = getattr(p, "ocr", None)
        if p.kind != "SCAN" or not t or len(t.strip()) < 40:
            continue
        q = classify(Page(p.file, p.no, t, 0))
        sc = sorted(q.score.values(), reverse=True)
        margin = sc[0] - (sc[1] if len(sc) > 1 else 0)
        _drop = drop_kinds()
        if q.kind == "PO" or (q.kind in SPLIT_KEYS and margin < 2) or (q.kind in _drop and sc[0] < 5):
            # OCR 글자가 깨지면 낱말 점수가 흔들린다. 확신이 없으면 스캔 묶음(전체 칸)으로 남긴다.
            # 항공 26번 인보이스가 구매주문서로 버려지고 포장명세서가 선하증권으로 갈린 적이 있다
            p.image, p.routed = True, "scan"
            continue
        if q.kind in _drop:
            p.kind, p.routed = q.kind, "drop"
        elif q.kind in SPLIT_KEYS:
            p.kind, p.image, p.sea, p.routed = q.kind, True, q.sea, q.kind
        elif sum(q.score.get(k, 0) for k in SPLIT_KEYS) >= 2:
            # 서류 낱말이 조금은 있는데 종류를 못 가른다. 버리지 않고 스캔 묶음(전체 칸)으로 읽는다
            p.image, p.routed = True, "scan"
        else:
            # 글은 읽히는데 아는 서류가 아니다(실린더 목록, 컨테이너 점검표, 부착 목록). 모델에 안 보낸다
            p.kind, p.routed = "TERMS", "other"
    for p in pages:
        if p.kind == "SCAN" and getattr(p, "png", "") and getattr(p, "ocr_engine", "") and len((p.ocr or "").strip()) < 40:
            p.kind, p.routed = "TERMS", "blank"      # OCR 이 글을 거의 못 찾은 쪽. 사진이나 빈 쪽이다
    if not any(p.kind in SPLIT_KEYS or p.kind == "SCAN" or (p.kind == "OTHER" and not p.needs_image) for p in pages):
        # 전부 버리면 보낼 쪽이 없다. 한 쪽짜리 스캔 꾸러미가 그렇게 실패했다(항공 1건). 가르지 못한 쪽은 스캔으로 되돌려 읽는다
        for p in pages:
            if getattr(p, "routed", "") in ("other", "blank"):
                p.kind, p.image, p.routed = "SCAN", True, "scan"
    return pages


def scan_images(pages, dest, dpi=110):
    """스캔 쪽을 PNG 로 그려 뜻 없는 이름으로 둔다. PDF 를 그대로 주면 CLI 가 poppler 를 찾다
    멈춘다(실측: "I need to install poppler"). PNG 는 Read 가 바로 본다. 스캔 쪽만 그리니
    여덟 쪽짜리 PDF 에서 두 쪽만 읽는다."""
    os.makedirs(dest, exist_ok=True)
    out = []
    for i, p in enumerate([p for p in pages if p.needs_image], 1):
        if getattr(p, "png", None) and os.path.exists(p.png):
            out.append(p.png); continue          # OCR 하려고 이미 그려 둔 그림
        w, h, png = render_scan(p, dpi=dpi)
        dst = os.path.join(dest, "page%02d.png" % i)
        with open(dst, "wb") as f:
            f.write(png)
        out.append(dst)
    return out


def neutral_copies(paths, dest):
    """서류를 뜻 없는 이름으로 베껴 둔다. 경로를 프롬프트에 넣어야 하기 때문이다.

    이것 때문에 실측이 한 번 틀렸다. 케이스 폴더 이름이 HAWB 번호라서, 모델이
    서류를 안 읽고 경로에서 번호를 가져왔다. 원문 대조가 `원문에 없는 값` 으로
    잡아 줬으니 망정이지, 잡히는 자리가 없었으면 맞은 줄 알았을 것이다.

    파일 이름에는 고객 이름과 BL 번호가 흔히 붙는다. 그것도 같이 사라진다.
    """
    os.makedirs(dest, exist_ok=True)
    out = []
    for i, p in enumerate(paths, 1):
        ext = os.path.splitext(p)[1].lower() or ".pdf"
        dst = os.path.join(dest, "doc%02d%s" % (i, ext))
        shutil.copyfile(p, dst)
        out.append(dst)
    return out


def cli_missing_message(binary="claude"):
    """`claude` 를 못 찾았을 때 무엇을 해야 하는지 알려 준다."""
    return (f"{binary} 를 찾지 못했습니다. 터미널에서는 되는데 여기서 안 되면 "
            f"이 파이썬이 물려받은 PATH 에 없는 것입니다. "
            f"`which {binary}` 로 경로를 확인한 뒤 그 경로를 --claude-bin 으로 주거나 "
            f"PATH 에 넣고 다시 돌리십시오.")


class RateLimited(RuntimeError):
    """계정 한도에 걸렸다. 다음 케이스를 불러도 같은 답이 오므로 부르는 쪽이 멈춰야 한다.

    실제로 그랬다. 한도에 걸린 뒤 케이스 8건을 더 불러 전부 실패로 세고 5분을 버렸다.
    """


def _cli_error(proc):
    """CLI 가 죽었을 때 이유를 찾는다. 종료 코드가 1 이어도 stdout 에 JSON 이 있을 때가 있다."""
    try:
        env = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        env = {}
    msg = str(env.get("result") or "")
    if env.get("api_error_status") == 429 or "limit" in msg.lower():
        return RateLimited(f"claude CLI 한도: {msg[:200]}")
    if msg:
        return RuntimeError(f"claude CLI 종료 코드 {proc.returncode}: {msg[:300]}")
    return RuntimeError(f"claude CLI 종료 코드 {proc.returncode}"
                        f" | stderr: {(proc.stderr or '').strip()[:400]!r}"
                        f" | stdout: {(proc.stdout or '').strip()[:200]!r}")


def _png_block(path):
    import base64
    with open(path, "rb") as f:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(f.read()).decode()}}


def _usage_from_events(events, model=None):
    """stream-json 사건에서 토큰을 모은다. 결과 사건이 오기 전에 끊으면 assistant 사건만 남는다."""
    res = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if res:
        u = res.get("usage") or {}
        return dict(prompt_tokens=u.get("input_tokens"), completion_tokens=u.get("output_tokens"),
                    cache_read=u.get("cache_read_input_tokens"), cache_write=u.get("cache_creation_input_tokens"),
                    cli_cost_usd=res.get("total_cost_usd"), turns=res.get("num_turns"))
    # 여기가 보통 길이다. out.json 이 생기면 결과 사건을 안 기다리고 CLI 를 끊기 때문이다.
    # 그래서 이 갈래가 조용히 틀리면 케이스당 실비가 통째로 틀린다. 실측으로 두 가지를 봤다.
    #
    # **같은 message.id 가 여러 번 온다.** 그대로 더하면 입력과 캐시가 두 배가 된다.
    # 한 message 당 한 번만 센다.
    #
    # **출력 토큰은 스트림에서 못 건진다.** assistant 사건이 들고 오는 output_tokens 는
    # 아직 쓰는 중인 값이라 8 이나 2 로 나온다. 같은 호출의 결과 사건은 1,331 이었다.
    # 받아 낸 JSON 으로 채우지만(`_fill_partial_usage`) 그것도 잰 값이 아니다.
    #
    # **그래서 실비를 아예 안 낸다.** 캐시 읽기로 갈 몫이 여기서는 입력과 캐시 쓰기 자리에
    # 잡힌다. 같은 케이스를 재 보니 끊고 세면 $0.5072, 끝까지 기다리면 $0.2259 였다.
    # 2.2배다. 틀린 숫자를 내놓느니 안 내놓는 편이 낫다. 비용을 재려면 WAIT_RESULT 로 돈다.
    seen = {}
    for e in events:
        if e.get("type") != "assistant": continue
        m = e.get("message") or {}
        seen[m.get("id") or id(e)] = m.get("usage") or {}
    tot = dict(prompt_tokens=0, completion_tokens=0, cache_read=0, cache_write=0)
    for u in seen.values():
        tot["prompt_tokens"] += u.get("input_tokens") or 0
        tot["completion_tokens"] += u.get("output_tokens") or 0
        tot["cache_read"] += u.get("cache_read_input_tokens") or 0
        tot["cache_write"] += u.get("cache_creation_input_tokens") or 0
    tot["turns"] = len(seen)
    tot["usage_partial"] = True
    tot["cli_cost_usd"] = None
    return tot


def _fill_partial_usage(u, raw, model=None):
    """스트림에서 못 건진 출력 토큰을 실제로 받아 낸 JSON 으로 채운다.

    도구 인자로 오는 JSON 이 곧 출력이므로 그 길이가 출력 토큰에 가깝다. 실비는 여기서도
    안 낸다. 입력 쪽이 어느 자리에 잡혔는지를 모르기 때문이다.
    """
    if not u.get("usage_partial") or not isinstance(raw, dict):
        return u
    n = est_tokens_text(json.dumps(raw, ensure_ascii=False))
    return dict(u, completion_tokens=n) if n > (u.get("completion_tokens") or 0) else u


def lenient_schema(sc):
    """도구에 줄 스키마를 느슨하게 편다. 숫자 칸에 "25,695.000KG" 같은 글이 오면 CLI 가 도구 호출을 거절하고,
    모델은 같은 호출을 여섯 번 되풀이하다 빈손으로 끝났다(항공 30번, 41초). 글과 널도 받아 두고 형은 후처리가 맞춘다."""
    if isinstance(sc, dict):
        out = {}
        for k, v in sc.items():
            if k == "type" and v in ("number", "integer", "string"):
                out[k] = [v, "string", "null"] if v != "string" else ["string", "null"]
            elif k == "type" and v == "array":
                out[k] = ["array", "null"]
            elif k in ("required", "additionalProperties"):
                continue
            else:
                out[k] = lenient_schema(v)
        return out
    if isinstance(sc, list):
        return [lenient_schema(x) for x in sc]
    return sc


def call_claude_cli(model, messages, paths=None, timeout=300, binary="claude", think=False, config_dir=None, schema=None):
    """Claude Code CLI 를 서브프로세스로 부른다. API 키 없이 구독으로 돈다.

    **스키마를 강제한다.** 스키마를 박은 MCP 도구(`tools/extract_mcp.py`)를 띄우고 그
    도구를 부르게 한다. API 의 tool_choice 와 같은 얼개이고 전달 수단만 다르다.

    **그림은 메시지에 바로 싣는다(stream-json).** 앞 판은 PNG 경로를 주고 CLI 의 Read 로
    열게 했다. 쪽마다 왕복이 생겨 스캔 넉 장짜리 해상 꾸러미 하나가 100초 걸렸다. 같은
    그림을 메시지에 base64 로 실으니 한 번에 읽고 11.6초였다(출력 3,906 토큰이 462 토큰).

    **도구를 부르면 바로 끊는다.** 도구 결과를 받은 모델이 맺음말을 한 번 더 쓰는데, 그
    답은 버린다. 결과 파일이 생기는 순간 프로세스를 끝낸다.

    **생각 토큰을 끈다.** 서류에 적힌 숫자를 옮겨 적는 일이라 따져 볼 것이 없다.
    """
    import signal
    sys_txt = messages[0]["content"]
    paths = [os.path.abspath(x) for x in (paths or [])]
    work = tempfile.mkdtemp(prefix="docpipe-")
    try:
        sc = os.path.join(work, "schema.json")
        out = os.path.join(work, "out.json")
        cfg = os.path.join(work, "mcp.json")
        with open(sc, "w", encoding="utf-8") as f:
            json.dump(lenient_schema(schema or SCHEMA_STRICT), f, ensure_ascii=False)
        server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_mcp.py")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"ex": {
                "command": sys.executable, "args": [server],
                "env": {"EXTRACT_SCHEMA": sc, "EXTRACT_OUT": out}}}}, f)
        body = usr_text(messages)
        need_img = "[scanned page, no text layer]" in body
        pngs = [x for x in paths if x.lower().endswith(".png")]
        content = []
        if need_img and pngs:
            content = [_png_block(x) for x in pngs]
            prompt = (f"{sys_txt}\n\n{body}\n\nPages marked [scanned page, no text layer] have no text layer. "
                      f"They are attached above as images, one image per such page in the same order. "
                      f"Call the mcp__ex__extract tool exactly once with everything. Every value must come from inside "
                      f"a document. Do not reply with prose.")
        else:
            prompt = (f"{sys_txt}\n\n{body}\n\nCall the mcp__ex__extract tool exactly once with "
                      f"everything above. Do not reply with prose.")
        content.append({"type": "text", "text": prompt})
        # Windows 는 npm 이 깐 claude.cmd 라 이름만으로는 Popen 이 못 찾는다(이관 10/29). PATH 에서 실제 파일을 찾는다
        binary = shutil.which(binary) or binary
        cmd = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--mcp-config", cfg, "--strict-mcp-config", "--allowed-tools", "mcp__ex__extract"]
        if model:
            cmd += ["--model", model]
        env = dict(os.environ)
        # MCP 도구를 미뤄 두지 않는다. 미뤄 두면 모델이 첫 턴에 ToolSearch 로 스키마를 불러오느라 왕복이 하나 는다(2초)
        env["ENABLE_TOOL_SEARCH"] = "false"
        if not think:
            env["MAX_THINKING_TOKENS"] = "0"
        if config_dir:
            env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
        t0 = time.perf_counter()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", cwd=work, env=env, start_new_session=True)  # Windows 기본 cp949 로 읽지 않게
        except FileNotFoundError:
            raise RuntimeError(cli_missing_message(binary))
        events, err_lines, marks = [], [], {}
        import threading
        def pump_out():
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try: e = json.loads(line)
                    except json.JSONDecodeError: continue
                    e["_t"] = round(time.perf_counter() - t0, 2)
                    events.append(e)
                    if e.get("type") == "assistant":
                        for c in (e.get("message") or {}).get("content") or []:
                            if not isinstance(c, dict): continue
                            if c.get("type") == "text": marks["prose_chars"] = marks.get("prose_chars", 0) + len(c.get("text") or "")
                            if c.get("type") == "tool_use":
                                marks.setdefault("tools", []).append((c.get("name"), e["_t"]))
        def pump_err():
            for line in proc.stderr: err_lines.append(line)
        th = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
        for t in th: t.start()
        try:
            proc.stdin.write(msg); proc.stdin.close()
        except BrokenPipeError:
            pass
        stopped_early = False
        while True:
            # out.json 이 생기면 결과 사건을 안 기다리고 끊는다. 빠른 대신 CLI 가 적어 주는
            # 토큰과 실비를 못 받는다. 비용을 잴 때는 DOCPIPE_WAIT_RESULT=1 로 끝까지 기다린다.
            if os.path.exists(out) and not WAIT_RESULT:
                stopped_early = proc.poll() is None
                break
            if proc.poll() is not None:
                break
            if time.perf_counter() - t0 > timeout:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except Exception: proc.kill()
                raise RuntimeError(f"claude CLI 가 {timeout}초 안에 안 끝났습니다")
            time.sleep(0.03)
        wall = (time.perf_counter() - t0) * 1000
        if stopped_early:
            try: os.killpg(proc.pid, signal.SIGTERM)
            except Exception: proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except Exception: proc.kill()
        for t in th: t.join(timeout=2)
        res = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if not os.path.exists(out):
            if res and res.get("is_error"):
                if res.get("api_error_status") == 429:
                    raise RateLimited(f"claude CLI 한도: {str(res.get('result'))[:200]}")
                raise RuntimeError(f"claude CLI 오류: {str(res.get('result'))[:400]}")
            if proc.returncode not in (0, None) and not res:
                blob = "".join(err_lines)[-600:]
                if re.search(r"(?i)usage limit|rate limit|429|limit reached", blob):
                    raise RateLimited(f"claude CLI 한도: {blob[-200:]}")
                raise RuntimeError(f"claude CLI 가 {proc.returncode} 로 끝났습니다 | stderr: {blob[-300:]!r}")
            raw = _json_from_text((res or {}).get("result") or "")
        else:
            with open(out, encoding="utf-8") as f:
                raw = json.load(f)
        u = _fill_partial_usage(_usage_from_events(events, model), raw, model)
        init = next((e["_t"] for e in events if e.get("type") == "system"), None)
        return raw, dict(wall=wall, used_tool=os.path.exists(out), read_files=False, images=len(content) - 1,
                         stopped_early=stopped_early, think=think, t_init=init,
                         prose_chars=marks.get("prose_chars", 0), tools=marks.get("tools"), **u)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- 3.5 서류별로 나눠 부르기 ----------
# 꾸러미 전체를 한 번에 주면 서류 사이에서 값이 새어 들어온다. 인보이스의 Sold to 가 AWB 의
# 수하인 자리에 오고, 두 인보이스의 총액을 모델이 더하다 틀린다(28301.8 과 28271.2. 맞는 값은
# 28270.8). 서류 하나씩 따로 부르면 각 호출이 보는 글이 짧고, 인보이스마다 찍힌 총액을 그대로
# 받아 우리가 더한다. 호출은 늘지만 동시에 부르므로 벽시계는 오히려 준다.
SPLIT = True
SPLIT_WORKERS = 10     # 서류 7개짜리 해상 꾸러미에서 일곱째 호출이 빈자리를 10초 기다렸다
SCAN_CHUNK = 4

# 🔴 좌표 파서를 껐다 켤 수 있어야 하는 이유. 양식이 고정된 AWB 는 파서가 좌표로 읽고
#    모델을 아예 안 부른다. 정답 20건의 운송장 칸 330자리 중 329자리를 맞히므로 운영에서는
#    켜 두는 것이 맞다. 다만 그 값은 "모델의 성적" 이 아니라 "모델에 우리 파서를 붙인
#    성적" 이다. 두 값을 한 표에 섞으면 모델 비교가 아니게 된다. 모델만 재려면 끈다.
USE_AWB_PARSER = True
# 스캔 쪽을 CLI 에 그림으로 붙일 것인가. 끄면 OCR 글자만 간다. 그림을 못 받는 모델과
# 같은 자에 세우려고 끈다(29번 문서의 C 얼개). 기본은 켬이라 옛 배치와 같은 값이 나온다.
SEND_SCAN_IMAGES = True


def set_scan_images(on=True):
    """스캔 쪽 그림 첨부를 켜거나 끈다. CLI 경로에만 걸린다."""
    global SEND_SCAN_IMAGES
    SEND_SCAN_IMAGES = bool(on)


def set_awb_parser(on=True):
    global USE_AWB_PARSER
    USE_AWB_PARSER = bool(on)
SPLIT_KEYS = {
    "AWB": ["hawb", "mawb", "origin", "destination", "flight1", "customer_po", "item_desc",
            "shipper_name", "shipper_address", "consignee_name", "consignee_address",
            "awb_pieces", "awb_gross", "awb_chargeable", "awb_declared_value", "dims_cm", "package_unit",
            "agent_iata_code", "agent_account_no", "freight_term", "awb_weight_charge", "awb_valuation_charge",
            "awb_tax", "awb_other_charges_agent", "awb_other_charges_carrier", "awb_total", "measurement_cbm",
            "invoice_no", "po_list"],
    "CI":  ["invoice_no", "incoterms", "currency", "customer_po", "item_desc", "ci_lines", "ci_total",
            "ci_gross", "ci_net", "shipper_name", "shipper_address", "consignee_name", "consignee_address",
            "origin", "destination", "pl_packages", "package_unit", "ci_tax",
            # 하이닉스 판에만 있는 칸. 다른 판의 스키마에는 없으므로 sub_schema 가 걸러 낸다.
            "po_list", "invoice_totals"],
    "PL":  ["pl_lines", "pl_packages", "pl_gross", "pl_net", "package_unit", "dims_cm", "measurement_cbm",
            "invoice_no", "customer_po", "po_list"],
    "LCA": ["lca_lines", "lca_total_issue", "lca_grand_total_usd"],
    # 하이닉스 판에서만 보낸다. 다른 판의 스키마에는 cn_lines 가 없어 sub_schema 가 걸러 낸다.
    "CUSTOMS": ["cn_lines", "invoice_no", "customer_po"],
}
# 같은 키를 여러 서류가 낼 때 누가 이기는가. 키의 주인 서류가 먼저고, 나머지는 이 순서다.
PREFER = {"AWB": 0, "CI": 1, "PL": 2, "LCA": 3, "CUSTOMS": 3, "SCAN": 4}
OWNER = {"CI": ("currency", "incoterms", "invoice_no", "customer_po", "ci_lines", "ci_total", "ci_gross", "ci_net"),
         "PL": ("pl_lines", "pl_packages", "pl_gross", "pl_net", "package_unit"),
         "LCA": ("lca_lines", "lca_total_issue", "lca_grand_total_usd"),
         "CUSTOMS": ("cn_lines",)}
KEY_OWNER = {k: kind for kind, ks in OWNER.items() for k in ks}


def _rank(kind, key):
    """이 키를 이 서류가 얼마나 앞서 말하는가. 주인이면 -1. 스캔 묶음은 인보이스나 포장명세서가
    그림으로만 있을 때 그 서류 몫이므로, 주인이 있는 키에서는 주인 다음이고 AWB 보다 앞이다."""
    if KEY_OWNER.get(key) == kind: return -1
    if kind == "SCAN" and key in KEY_OWNER: return -0.5
    return PREFER.get(kind, 9)
# 서류가 여럿이면(인보이스 둘) 더하는 키와 이어 붙이는 키
SUM_KEYS = {"ci_total", "ci_gross", "ci_net", "pl_packages", "pl_gross", "pl_net", "ci_tax"}
CAT_KEYS = {"ci_lines", "pl_lines", "dims_cm", "lca_lines", "po_list", "invoice_totals", "cn_lines"}


def split_groups(pages):
    """분류된 쪽을 서류 단위로 묶는다. 같은 종류가 이어지면 한 서류다. 스캔 쪽은 통째로 한 묶음이다."""
    groups, cur, cur_hint, sea_grp = [], None, None, None
    mark_sea(pages)
    for p in pages:
        if getattr(p, "dropped", False):
            # 버리는 쪽(통관표, 약관)은 안 보내지만 서류의 경계다. 인보이스 둘 사이의 통관표를
            # 빼 버리면 두 인보이스가 한 묶음이 된다.
            cur, cur_hint = None, None
            continue
        if p.kind == "AWB" and getattr(p, "sea", False):
            # 해상 꾸러미의 선하증권은 하우스와 마스터가 따로 온다. 따로 부르면 저마다 제 번호를
            # 하우스라고 내므로 한 번에 주고 모델이 가리게 한다. 글자 쪽과 스캔 쪽이 섞여도 한 묶음이다.
            if sea_grp is None:
                sea_grp = ("AWB", [p]); groups.append(sea_grp)
            else:
                sea_grp[1].append(p)
            cur, cur_hint = sea_grp, None
            continue
        if p.image and p.kind in SPLIT_KEYS:
            # 밀린 글꼴 쪽. 종류는 되돌린 글의 힌트다. 힌트가 같은 쪽끼리 한 서류로 묶고 그림으로 읽는다.
            if cur and cur[0] == p.kind and cur_hint == "image":
                cur[1].append(p)
            else:
                cur, cur_hint = (p.kind, [p]), "image"; groups.append(cur)
            continue
        k = p.kind if p.kind in SPLIT_KEYS else ("SCAN" if p.kind == "SCAN" else None)
        if k is None:
            if p.image:
                # 종류를 못 가른 그림 쪽. 앞이 그림 묶음이면 뒷장이고, 아니면 전체 스키마로 따로 읽는다.
                # 글자 AWB 묶음에 붙이면 양식 파서가 모델을 안 부르므로 그 쪽을 아예 안 읽게 된다.
                if cur and any(q.needs_image for q in cur[1]): cur[1].append(p)
                else: cur, cur_hint = ("SCAN", [p]), None; groups.append(cur)
            elif cur: cur[1].append(p)      # OTHER 는 앞 서류의 뒷장으로 본다
            continue
        if k == "SCAN":
            if cur and cur[0] == "SCAN" and cur_hint is None: cur[1].append(p)
            else: cur, cur_hint = ("SCAN", [p]), None; groups.append(cur)
            continue
        if k == "AWB" and getattr(p, "sea", False):
            # 해상 꾸러미의 선하증권은 하우스와 마스터가 따로 온다. 따로 부르면 저마다 제 번호를
            # 하우스라고 내므로 한 번에 주고 모델이 가리게 한다. 사이에 낀 다른 서류와 무관하게 한 묶음이다.
            if sea_grp is None:
                sea_grp = ("AWB", [p]); groups.append(sea_grp)
            else:
                sea_grp[1].append(p)
            cur, cur_hint = sea_grp, None
            continue
        if cur and cur[0] == k and cur_hint is None and _same_doc(cur[1][-1], p):
            cur[1].append(p)
        else:
            cur, cur_hint = (k, [p]), None; groups.append(cur)
    return groups


def group_keys(kind, pages):
    """이 묶음이 채울 칸. 그림으로 읽는 인보이스나 포장명세서는 힌트가 틀릴 수 있어 둘의 칸을 다 준다."""
    if kind == "SCAN":
        return list(SCHEMA_STRICT["properties"])
    if any(p.needs_image for p in pages) and kind in ("CI", "PL"):
        return list(dict.fromkeys(SPLIT_KEYS["CI"] + SPLIT_KEYS["PL"]))
    if kind in ("CI", "PL") and any(re.search(r"invoice\s*(?:&|/|and)\s*packing\s*list", p.text, re.I) for p in pages):
        # 한 장짜리 "INVOICE & PACKING LIST". 어느 쪽으로 갈리든 두 서류의 칸을 다 채워야 한다(해상 19번은 인코텀즈와 통화를 잃었다)
        return list(dict.fromkeys(SPLIT_KEYS["CI"] + SPLIT_KEYS["PL"]))
    return list(SPLIT_KEYS[kind])


def _same_doc(a, b):
    """이어지는 같은 종류의 쪽이 같은 서류인가. 쪽 번호가 1/2, 2/2 처럼 이어지거나 머리글이 같으면 같다."""
    ha, hb = a.text.strip()[:120], b.text.strip()[:120]
    if ha and ha == hb: return True
    mb = re.search(r"\b(\d+)\s*(?:/|of)\s*(\d+)\b", b.text[:400], re.I)
    if mb and mb.group(1) != "1": return True
    return not re.search(r"(?i)\b(commercial invoice|packing list|invoice no)\b", b.text[:300]) \
        or ("TOTAL" not in a.text.upper())


LINE_DESC = False   # 줄의 품명은 뒤에서 아무도 안 읽는다. 빼면 출력 토큰이 줄고 호출이 그만큼 빨라진다


def _lean_lines(v):
    if isinstance(v, dict) and v.get("type") == "array" and isinstance(v.get("items"), dict) \
            and "description" in (v["items"].get("properties") or {}):
        it = dict(v["items"])
        it["properties"] = {k: x for k, x in it["properties"].items() if k != "description"}
        it["required"] = [k for k in it.get("required", []) if k != "description"]
        return {**v, "items": it}
    return v


def sub_schema(kind, schema=None, keys=None):
    sc = schema or SCHEMA_STRICT
    keys = keys or SPLIT_KEYS.get(kind) or list(sc["properties"])
    props = {k: (v if LINE_DESC else _lean_lines(v)) for k, v in sc["properties"].items() if k in keys}
    return {**sc, "properties": props, "required": [k for k in sc.get("required", []) if k in props]}


def api_equiv_usd(system_txt, body_txt, n_images, raw, model="sonnet-5"):
    """같은 호출을 API 로 했을 때의 값. CLI 는 Claude Code 자체 프롬프트(약 4만 토큰)가 붙어 실비가 부풀므로
    운영 경로(API) 기준으로 환산한다. 시스템 프롬프트는 캐시 읽기(0.1배), 그림 한 장은 약 1,600 토큰으로 친다."""
    pi, po = PRICE.get(model, PRICE["sonnet-5"])
    sys_tok = len(system_txt or "") / 3.5
    in_tok = len(body_txt or "") / 3.5 + n_images * 1600
    out_tok = len(json.dumps(raw or {}, ensure_ascii=False)) / 3.2
    return (sys_tok * pi * 0.1 + in_tok * pi + out_tok * po) / 1e6


def call_split(model, pages, paths, how, caller="cli"):
    """서류별로 나눠 동시에 부르고 하나로 합친다. how 는 부르는 쪽에 넘길 인자.

    caller 가 "anthropic" 이면 같은 나누기를 API 로 부르고, "openai" 면 OpenAI 호환
    서버(솔라, llama-server, vLLM)로 부른다. how 는 caller 에 따라 다르다.

        cli          call_claude_cli 의 인자 (binary, think, config_dir)
        anthropic    안 쓴다
        openai       {"base": "https://api.upstage.ai/v1", "key": ..., "vision": False}
        upstage_ie   안 쓴다. 프롬프트를 받지 않는 경로라 쪽과 부분 스키마만 간다

    나누기는 정답 대조를 78.5% 에서 98.1% 로 올린 얼개인데 CLI 에만 걸려 있었다.
    벤치마크에서 클로드는 나눠 부르고 솔라는 한 번에 불렀으니 그 표는 모델 비교가
    아니라 얼개 비교였다. 같은 자를 대려고 네 경로를 여기로 모은다.
    """
    from concurrent.futures import ThreadPoolExecutor
    groups = []
    for doc_id, (kind, pg) in enumerate(split_groups(pages)):
        keys = group_keys(kind, pg)
        if any(p.needs_image for p in pg) and len(pg) > SCAN_CHUNK:
            # 열두 쪽을 한 번에 그림으로 주니 포장명세서 총계를 빠뜨렸다. 서너 쪽씩 나눠 동시에 부른다.
            # 같은 서류의 조각이므로 doc_id 가 같고, 합칠 때 총계를 더하지 않는다.
            groups += [(kind, pg[i:i + SCAN_CHUNK], doc_id, keys) for i in range(0, len(pg), SCAN_CHUNK)]
        else:
            groups.append((kind, pg, doc_id, keys))
    if not groups:
        raise RuntimeError("보낼 쪽이 없습니다")
    def one(g):
        kind, pg, doc_id, keys = g
        if USE_AWB_PARSER and kind == "AWB" and not any(p.needs_image or getattr(p, "sea", False) for p in pg):
            parsed = parse_awb_form(pg[0])
            if parsed:
                # 양식이 고정된 AWB 는 좌표로 읽는다. 모델을 안 부르니 0초이고 회차마다 같다.
                raw = {k: v for k, v in parsed.items() if k in keys}
                return kind, raw, dict(wall=0, prompt_tokens=0, completion_tokens=0, used_tool=True,
                                       form=parsed.get("_form")), doc_id, keys
        if caller == "upstage_ie":
            # 정보추출은 프롬프트를 받지 않는다. 고른 쪽과 그 서류의 칸만 보낸다.
            # 칸의 뜻은 FIELD_DOC 가 스키마에 적어 두었으므로 이 경로도 정의를 읽는다.
            raw, st = call_upstage_ie(pg, schema=sub_schema(kind, keys=keys))
            return kind, raw, st, doc_id, keys
        image = any(p.needs_image for p in pg)
        # Anthropic API 는 스캔 쪽을 늘 그림으로 실어 왔다. 그러면 글자만 받는 모델과 같은
        # 자에 못 선다. 다른 경로와 같은 스위치를 본다. 켜 두면 앞과 같은 동작이다.
        see = (caller == "anthropic" and SEND_SCAN_IMAGES) or (caller == "openai" and how.get("vision"))
        msgs = build_messages(pg, vision=(see and image))
        sea = any(getattr(p, "sea", False) for p in pg)
        if kind == "SCAN":
            head = ("This call covers the scanned pages of a bundle. Read them as images and fill every field they print. "
                    "If they are a sea bundle with two bills of lading, hawb, shipper, consignee, origin, destination and freight_term come from the HOUSE B/L "
                    "(the forwarder's B/L whose shipper is the seller); a page marked CHECK B/L or whose shipper reads 'X O/B OF Y' is the MASTER B/L and gives only mawb.\n\n")
        elif kind == "AWB" and sea:
            head = (f"This call covers the bill(s) of lading of a SEA bundle ({len(pg)} page(s): the house B/L, and the master B/L or its rider when present). "
                    f"Fill only these fields: {', '.join(keys)}. Use null for anything not printed.\n\n")
        else:
            head = (f"This call covers one {kind} document ({len(pg)} page(s)) of a bundle"
                    + (", given as images" if image else "") + ". "
                    f"Fill only these fields from this document: {', '.join(keys)}. "
                    f"Use null or an empty array for anything this document does not print. "
                    f"Do not put a packing-list weight into an invoice field or an invoice amount into a packing-list field.\n\n")
        msgs[0] = dict(msgs[0], content=msgs[0]["content"] + "\n" + head)
        sc = sub_schema(kind, keys=keys)
        if caller == "anthropic":
            raw, st = call_anthropic(model, msgs, schema=sc)
            return kind, raw, st, doc_id, keys
        if caller == "openai":
            base = how["base"]
            # 로컬 서버는 널을 받는 스키마라야 문법 강제가 걸린다. 원격은 엄격판이다.
            local = "127.0.0.1" in base or "localhost" in base
            raw, st = call_openai_compat(base, model, msgs, key=how.get("key"),
                                         schema=sub_schema(kind, schema=(SCHEMA if local else SCHEMA_STRICT), keys=keys))
            return kind, raw, st, doc_id, keys
        imgs = tempfile.mkdtemp(prefix="docpipe-img-")
        try:
            shots = scan_images(pg, imgs) if SEND_SCAN_IMAGES else []
            raw, st = call_claude_cli(model, msgs, paths=shots, schema=sc, **how)
            st = dict(st, api_usd=api_equiv_usd(msgs[0]["content"], usr_text(msgs), sum(1 for p in pg if p.needs_image), raw))
            # 🔴 통관 용도설명서의 코드 열이 통째로 비면 한 번 더 부른다. 그 표가 Material 의
            #    유일한 출처인 서류가 있고, 스캔이라 회차마다 되고 안 되고 한다. 실측에서
            #    같은 서류가 한 회차는 17행 전부, 다음 회차는 0행이었다. 성공한 회차에는
            #    이 비용이 안 든다.
            if kind == "CUSTOMS" and isinstance(raw, dict):
                cl = [x for x in (raw.get("cn_lines") or []) if isinstance(x, dict)]
                if cl and not any(str(x.get("sap_code") or "").strip() for x in cl):
                    raw2, st2 = call_claude_cli(model, msgs, paths=shots, schema=sc, **how)
                    cl2 = [x for x in ((raw2 or {}).get("cn_lines") or []) if isinstance(x, dict)]
                    if any(str(x.get("sap_code") or "").strip() for x in cl2):
                        raw, st = raw2, dict(st2, retried=1,
                                             wall=(st.get("wall") or 0) + (st2.get("wall") or 0))
            if image and not any(v not in (None, "", []) for v in (raw or {}).values()) and not st.get("error") and (st.get("wall") or 0) >= 1000:
                # 빈 답. 스캔 4쪽짜리 해상 꾸러미가 71초 만에 {} 를 낸 적이 있다. 한 번 더 부른다
                raw2, st2 = call_claude_cli(model, msgs, paths=shots, schema=sc, **how)
                if raw2: raw, st = raw2, dict(st2, retried=1, wall=(st.get("wall") or 0) + (st2.get("wall") or 0))
        finally:
            shutil.rmtree(imgs, ignore_errors=True)
        return kind, raw, st, doc_id, keys
    with ThreadPoolExecutor(max_workers=SPLIT_WORKERS) as pool:
        parts = list(pool.map(one, groups))
    raw = merge_parts([(k, r, d, ks) for k, r, _, d, ks in parts])
    stats = [st for _, _, st, _, _ in parts]
    # 부른 값을 다 더해 둔다. 앞 판은 토큰만 더하고 cli_cost_usd 와 캐시 두 칸을 흘렸다.
    # 그래서 나눠 부르기로 바꾼 뒤의 케이스당 실비를 한 번도 못 쟀다. 여기서 더하면
    # batch_run.py 가 케이스마다 그대로 적는다.
    def _sum(key):
        return sum(x.get(key) or 0 for x in stats)
    stat = dict(wall=max(x.get("wall") or 0 for x in stats),
                wall_sum=_sum("wall"),
                prompt_tokens=_sum("prompt_tokens"),
                completion_tokens=_sum("completion_tokens"),
                cache_read=_sum("cache_read"),
                cache_write=_sum("cache_write"),
                # 한 호출이라도 결과 사건을 못 받았으면 꾸러미 합도 잰 값이 아니다. 그 호출 몫이
                # 0 으로 들어가 버리므로 합을 내지 않고 비운다.
                cli_cost_usd=(None if any(x.get("usage_partial") for x in stats) else _sum("cli_cost_usd")),
                usd=_sum("usd"),
                usage_partial=any(x.get("usage_partial") for x in stats),
                api_usd=round(_sum("api_usd") or _sum("usd"), 4),
                calls=sum(1 for st in stats if st.get("wall")), groups=" ".join(k for k, _, _, _, _ in parts),
                # 끊긴 답을 살린 호출이 몇 개였나. 합칠 때 빠뜨리면 표를 보는 사람이
                # 그 케이스의 값이 온전한 것인지 알 수가 없다.
                salvaged=sum(1 for st in stats if st.get("salvaged")) or None,
                form_hits=sum(1 for st in stats if st.get("form")),
                forms=" ".join(st["form"] for st in stats if st.get("form")),
                used_tool=all(x.get("used_tool") for x in stats), split=True,
                per_call=[dict(kind=k, pages=len(pg_), wall=round((st.get("wall") or 0) / 1000, 1), images=st.get("images"),
                               out_tok=st.get("completion_tokens"), prose=st.get("prose_chars"), tools=st.get("tools"),
                               form=st.get("form")) for (k, _, st, _, _), (_, pg_, _, _) in zip(parts, groups)])
    return raw, stat


_COPY_ID_KEYS = {"invoice_no", "po_no", "po_item_no", "case_no", "hs_code"}
_CODE_PAIR = ("part_no", "material_code", "sap_code")


def _row_sig(x):
    """줄 하나에서 사본 비교에 쓰는 칸만 남긴다. 숫자 칸(수량, 단가, 금액, 중량)과 번호 칸이다.

    품명, 모델, 원산지, 단위 같은 글자 칸은 스캔 사본마다 OCR 이 다르게 읽는다. 차례(seq)는 사본에
    따라 있다 없다 하고 뜻이 없다(실제 서류 11/6).
    """
    sig = {}
    # 🔴 Part No, Material Code, SAP 코드는 사본마다 칸이 서로 바뀌어 읽힌다(PNS26025031 은 앞의 둘이 맞바뀌고,
    # PNS26071952 는 SAP 코드가 Material Code 칸에 들어감, 11/10). 그래서 칸 이름을 떼고 코드를 한 묶음으로
    # 견준다. 숫자로만 된 코드도 숫자 칸으로 보내지 않는다.
    codes = frozenset(_norm_code(x.get(k)) for k in _CODE_PAIR if x.get(k) not in (None, ""))
    if codes:
        sig["_codes"] = codes
    for k, val in x.items():
        if val in (None, "") or isinstance(val, bool) or k == "seq" or k in _CODE_PAIR:
            continue
        n = num(val)
        if n is None and isinstance(val, str):
            # "0.24KG", "1.0 kg" 처럼 단위가 붙은 숫자도 숫자다
            m = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*[A-Za-z%]{0,4}\s*$", val)
            if m:
                n = num(m.group(1))
        if n is not None:
            sig[k] = n
        elif k in _COPY_ID_KEYS:
            sig[k] = _norm_code(val)
    return sig


def _norm_code(val):
    """OCR 이 붙임표를 다른 글자(‐, –)로 읽거나 띄어쓰기를 넣어도 같은 번호다."""
    return re.sub(r"[‐-―−-]+", "-", str(val)).replace(" ", "").upper()


def _sig_eq(k, a, b):
    if k == "_codes":
        # 한 사본은 Part No 를 OCR 이 틀리게 읽기도 해서(11/6), 두 코드 중 하나라도 겹치면 같다고 본다.
        # 견줄 숫자 칸이 줄마다 함께 맞아야 하므로 코드 하나만으로 사본이 되지는 않는다
        return bool(a & b)
    return a == b


def _same_lines(a, b):
    """두 줄 목록이 같은 서류의 사본인가.

    줄 수가 같고, 줄마다 둘 다 가진 숫자 칸과 번호 칸이 모두 같아야 한다. 한쪽에만 있는 칸(한 사본만
    case_no 나 part_no 를 읽은 경우)은 견주지 않는다. 견준 칸이 목록 전체로 둘이 안 되면 근거가
    모자라므로 다른 목록으로 본다.
    """
    if not a or len(a) != len(b):
        return False
    compared = 0
    code_typo = 0
    for x, y in zip(a, b):
        if isinstance(x, dict) != isinstance(y, dict):
            return False
        if not isinstance(x, dict):
            if str(x) != str(y):
                return False
            compared += 1
            continue
        sx, sy = _row_sig(x), _row_sig(y)
        shared = [k for k in sx if k in sy]
        bad = [k for k in shared if not _sig_eq(k, sx[k], sy[k])]
        if bad == ["_codes"] and len(shared) >= 3 and len(a) >= 3:
            # 한 사본이 코드 한 글자를 잘못 읽은 줄(5105-200527 과 5105-300527, PNS26071952, 11/10).
            # 숫자 칸이 다 맞으면 목록 전체에서 한 줄까지만 봐준다. 두 줄 넘게 다르면 다른 서류다
            code_typo += 1
            if code_typo > 1:
                return False
        elif not shared or bad:
            return False
        compared += len(shared)
    return compared >= 2


def _cat_unique(cur, v, seen):
    """배열을 잇되, 이미 이은 목록과 같은 사본인 목록은 다시 잇지 않는다. seen 은 이은 목록들이다.

    🔴 스캔 묶음에는 같은 인보이스 사본이 서너 부 들어온다(HB, OH, LCA 사본). 사본마다 줄을 읽어
       그대로 이으면 3줄이 9줄이 되고 금액 합이 총액의 3배가 되어 「빠진 줄 의심」이 뜬다
       (실제 서류 46건 점검 11/6, 스캔 묶음 11건이 모두 이것). 긴 표를 쪽마다 나눠 부른 조각은
       줄이 서로 다르므로 그대로 이어진다.
    """
    if any(_same_lines(prev, v) for prev in seen):
        return cur
    seen.append(v)
    return cur + v


def _merge_chunks(raws):
    """같은 서류의 조각들을 하나로. 배열은 잇고(똑같은 사본은 한 번만) 나머지는 처음 나온 값이다. 총계는 더하지 않는다."""
    out = {}
    seen = {}
    for r in raws:
        for k, v in (r or {}).items():
            if v in (None, "", []): continue
            if k in CAT_KEYS and isinstance(v, list):
                out[k] = _cat_unique(out.get(k) or [], v, seen.setdefault(k, []))
            elif out.get(k) in (None, "", []):
                out[k] = v
    return out


def merge_parts(parts):
    """서류별 결과를 한 꼴로 합친다. 같은 키는 주인 서류가 이기고, 같은 종류의 서류가 둘이면 더하거나 잇는다.

    parts: [(kind, raw)] 또는 [(kind, raw, doc_id, keys)]. doc_id 가 같은 조각은 한 서류다.
    """
    norm = []
    for i, pt in enumerate(parts):
        kind, raw = pt[0], pt[1]
        doc_id = pt[2] if len(pt) > 2 else i
        keys = pt[3] if len(pt) > 3 else (SPLIT_KEYS.get(kind) or list(SCHEMA["properties"]))
        raw = {k: (None if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", "na", "-", "nil") else v)
               for k, v in (raw or {}).items()}
        norm.append((kind, raw, doc_id, keys))
    docs = {}     # (kind, doc_id) -> [raws]
    keys_of = {}
    seen_inv = {}
    for kind, raw, doc_id, keys in norm:
        # 같은 종류의 두 묶음이 같은 인보이스 번호를 내면 같은 서류의 조각이다(포장명세서가 인보이스로
        # 갈린 경우). 더하면 총액과 포장 수가 두 배가 된다.
        inv = str(raw.get("invoice_no") or "").strip().upper()
        if inv and (kind, inv) in seen_inv:
            doc_id = seen_inv[(kind, inv)]
        elif inv:
            seen_inv[(kind, inv)] = doc_id
        docs.setdefault((kind, doc_id), []).append(raw)
        keys_of[kind] = list(dict.fromkeys(keys_of.get(kind, []) + list(keys)))
    by_kind = {}
    for (kind, doc_id), raws in docs.items():
        by_kind.setdefault(kind, []).append(_merge_chunks(raws))
    out = {}
    all_keys = list(SCHEMA["properties"])
    for k in all_keys:
        for kind in sorted(by_kind, key=lambda kd: _rank(kd, k)):
            raws = by_kind[kind]
            keys = keys_of.get(kind) or all_keys
            if k not in keys: continue
            vals = [r.get(k) for r in raws if r.get(k) not in (None, "", [])]
            if not vals: continue
            if k in CAT_KEYS:
                # 같은 종류의 서류가 여럿이면 줄을 잇는다. 번호가 없어 같은 서류로 못 묶인 사본(통관표 두 부)은
                # 줄 목록이 똑같으므로 한 번만 잇는다.
                v, seen = [], []
                for vv in vals:
                    if isinstance(vv, list):
                        v = _cat_unique(v, vv, seen)
            elif k in SUM_KEYS and len(raws) > 1:
                nums = [num(x) for x in vals]
                v = round(sum(n for n in nums if n is not None), 3) if any(n is not None for n in nums) else vals[0]
            else:
                v = vals[0]
            if out.get(k) in (None, "", []):
                out[k] = v
    return out


def _json_from_text(text):
    """모델이 낸 글에서 JSON 객체를 건져 낸다. 스키마를 강제 못 하는 길이라 필요하다."""
    t = text.strip()
    if t.startswith("```"):                       # ```json 울타리를 벗긴다
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a = t.find("{")
    if a < 0: raise ValueError(f"JSON 을 못 찾았습니다: {text[:200]!r}")
    depth, instr, esc = 0, False, False
    for i, c in enumerate(t[a:], a):              # 문자열 안의 중괄호를 세지 않는다
        if instr:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = False
            continue
        if c == '"': instr = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return json.loads(t[a:i + 1])
    raise ValueError(f"JSON 이 닫히지 않았습니다: {text[:200]!r}")


SCHEMA_DIR = wuxi_paths.SCHEMA
_UNITS = None

def load_units(path=None):
    """단위 낱말을 KEROL 코드로 바꾸는 표. `schema/unit-aliases.json` 이 정본이다.

    고객별 변칙(EA 라고 적지만 실제로는 박스)은 '확인' 이 true 인 줄만 쓴다.
    """
    global _UNITS
    if _UNITS is not None and path is None: return _UNITS
    p = path or os.path.join(SCHEMA_DIR, "unit-aliases.json")
    with open(p, encoding="utf-8") as fh: d = json.load(fh)
    u = {"pkg": {k.upper(): v for k, v in d["포장 단위"].items()},
         "contains": [(a.upper(), b) for a, b in d["낱말 포함"]],
         "weight": {k.upper(): v for k, v in d["무게 단위"].items()},
         "customer": {(str(r["고객"]).strip(), str(r["적힌 값"]).strip().upper()): r["쓸 코드"]
                      for r in d.get("고객별", []) if r.get("확인")}}
    if path is None: _UNITS = u
    return u

def unit_code(word, customer=None, units=None):
    """적힌 낱말 하나를 KEROL 코드로. 못 바꾸면 적힌 그대로 돌려준다."""
    if not word: return word
    u = units or load_units()
    key = re.sub(r"[^A-Z]", "", str(word).upper())
    if customer and (customer, key) in u["customer"]: return u["customer"][(customer, key)]
    if key in u["pkg"]: return u["pkg"][key]
    for w, c in u["contains"]:      # "PAPER BOXES", "Carton(s)", "1PLT" 처럼 낱말이 섞인 경우
        if w in key: return c
    return word

NULLISH = {"UNKNOWN", "NA", "NONE", "NULL", "NIL", "NOTFOUND", "NOTAVAILABLE", "NOTPROVIDED", "NOTSPECIFIED", "TBD", "미확인"}

SEA_BOILER = re.compile(r"""^\s*["'`]*\s*(?:SHIPPER['`\u2019]?S\s+LOAD(?:\s*[,&]|\s+AND)?\s*COUNT(?:\s*(?:&|AND)\s*(?:SEAL(?:ED)?|WEIGHT))?(?:\s*,\s*CONTENTS\s+UNKNOWN)?|SAID\s+TO\s+(?:CONTAIN|BE)|CY\s*/\s*CY|FCL(?:\s*/\s*FCL)?)\s*["'`]*\s*:?\s*""", re.I)
SEA_COUNT = re.compile(r"^\s*\d+\s*(?:X\s*)?(?:\d+['`]?\s*(?:GP|HC|HQ|RH|RF|RE|DRY|TK|DC|FT)\s*)?(?:PLASTICS?\s+|WOODEN\s+|STEEL\s+|PLYWOOD\s+)?(?:PKGS?|PACKAGES?|PALLETS?|PLTS?|DRUMS?|CASES?|CARTONS?|CTNS?|RACKS?|CRATES?|BOXES|BOX|BAGS?|ROLLS?|CYLINDERS?|CYLS?|UNITS?|CONTAINERS?|CNTRS?|BARRELS?|GALLONS?|GAL|BOTTLES?|BTLS?|KITS?|CANS?|PAILS?|SKIDS?|TOTES?|JARS?|TANKS?|PIECES?|PCS|SETS?|EA)\s*(?:\([^)]*\)\s*)?(?:OF\s+|:\s*)", re.I)
# 🔴 BARRELS 부터 EA 까지는 회사 docagent hx_item.QTY_HEAD 의 수량 낱말(10/29 이관 때 합침). 화학 원료와 부품 서류의 「4 DRUMS OF」 꼴 상용구가 품명에 남지 않게 한다


def clean_sea_desc(s):
    """선하증권의 품명 앞에 붙는 상용구(SHIPPER'S LOAD & COUNT, SAID TO CONTAIN, 8 PACKAGES OF)를 뗀다."""
    if not isinstance(s, str): return s
    prev = None
    while prev != s:
        prev = s
        s = SEA_BOILER.sub("", s); s = SEA_COUNT.sub("", s)
        s = re.sub(r"^\s*\d+\s*X\s*\d+['`]?\s*(?:GP|HC|HQ|RH|RF|RE|DRY|TK|DC|FT)\b\s*", "", s, flags=re.I)   # 1 X 22GP
        s = re.sub(r"^\s*(?:PACKAGES?|PALLETS?|CARTONS?|DRUMS?|CASES?)\s+OF\s+", "", s, flags=re.I)         # 수 없이 붙은 PACKAGES OF
    return s.strip(" :-\"'`") or None


MASTER_MARKS = [(r"check b/l", 3), (r"\bo/b\b", 3), (r"copy non-negotiable", 1), (r"\bm\.?r\.?n\b", 2), (r"call\s*sign", 2),
                (r"sinokor|\bsitc\b|weidong|evergreen|sinotrans|jinjiang|lyg ferry|yangtze river|shimonoseki ferry|container lines|merchant marine|ocean network|wan hai|\bcosco\b|\bkmtc\b|pan ocean|heung-a|namsung|dongjin shipping|\bhmm\b|\bone\b line", 2),
                (r"\bvgm\b", 2), (r"as agents? for", 1)]
HOUSE_MARKS = [(r"multimodal transport", 1), (r"for delivery of goods please apply", 2), (r"freight forwarders association", 2),
               (r"acting as a? ?carrier", 1), (r"house", 2), (r"document no", 1), (r"forwarding agent", 1), (r"h\.?b/l", 2)]
BL_LABEL = re.compile(r"(?<![A-Z.])(?:H\.?B/L\s*NO|HOUSE\s+B/L\s*NO|BILL OF LADING NO\.?|B/L\s*NO\.?|B/L\s*NUMBER|WAYBILL\s*NO\.?)")
MBL_LABEL = re.compile(r"(?:M\.?B/L\s*NO|MASTER\s+B/L\s*NO|DOCUMENT\s+NO\.?|CARRIER'?S\s+B/L\s*NO)")
BL_TOKEN = re.compile(r"(?<![A-Z0-9])([A-Z]{2,6}\d{5,14}|[A-Z]{3,6}[A-Z0-9]{6,14}\d)(?![A-Z0-9])")
NOT_BL = re.compile(r"^(?:USCI|TEL|FAX|VAT|CRN|CNE|MRN|UN\d|HS\d|ISO|IPPC|KR\d|CY|CFS|AEO|SNBU|SEKU|FCIU|BMOU|TWCU|FBIU|CKFU|SKRU|DYLU|ULDU|HPCU|UETU|WSDU|EMCU|KZCU|SNHU|BHCU|NSSU|TCLU|SEAL)")


def mark_sea(pages):
    """해상 꾸러미면 라벨 없는 선하증권 쪽(마스터 B/L 이 번호와 주소만 찍은 것)도 해상으로 친다."""
    if any(getattr(p, "sea", False) for p in pages):
        for p in pages:
            if p.kind == "AWB": p.sea = True
    return pages


def _after_label(text, label_rx):
    """라벨 뒤 150자 안에서 선하증권 번호로 볼 수 있는 첫 토큰. 번호가 다음 줄에 오거나 회사 이름이 사이에 끼기도 한다."""
    out = []
    for m in re.finditer(label_rx, text):
        rest = text[m.end():m.end() + 150]
        same_line = rest.split("\n", 1)[0]
        # 같은 줄의 번호가 먼저다. 칸이 나란한 양식에서는 다음 줄 첫 토큰이 다른 칸(회사 이름, 옆 칸 번호)이다
        for seg in (same_line, rest):
            hit = next((tok for tok in re.findall(r"[A-Z0-9]{7,20}", seg) if _bl_number(tok)), None)
            if hit: out.append(hit); break
    return out


def _bl_number(tok):
    """선하증권 번호로 볼 수 있는 토큰인가. 컨테이너 번호(4글자+7숫자, 앞 세 글자 뒤 U)와 등록번호는 뺀다."""
    if not tok or NOT_BL.match(tok.upper()): return False
    if re.fullmatch(r"[A-Z]{3}U\d{7}", tok.upper()): return False       # 컨테이너 번호
    return len(tok) >= 7 and len(re.findall(r"[A-Z]", tok.upper())) >= 2 and len(re.findall(r"\d", tok)) >= 4


def sea_numbers(pages):
    """글자층이 있는 선하증권 쪽에서 하우스와 마스터 번호 후보를 가른다.
    돌려주는 것: {"house": [번호...], "master": [번호...], "house_page": Page}. 스캔 쪽은 못 읽는다."""
    scored = []
    mark_sea(pages)
    for p in pages:
        if p.kind != "AWB" or not getattr(p, "sea", False): continue
        if p.needs_image and not getattr(p, "ocr", ""): continue
        t = _page_text(p).lower()
        m = sum(w for rx, w in MASTER_MARKS if re.search(rx, t))
        h = sum(w for rx, w in HOUSE_MARKS if re.search(rx, t))
        # "CHECK B/L" 같은 마스터 표지가 강하면 하우스 표지가 몇 개 있어도 마스터다(하우스 양식 문구가 베껴 찍히기도 한다)
        scored.append((h - m, p))
    if not scored: return None
    scored.sort(key=lambda x: -x[0])
    house_p = scored[0][1]
    from_ocr = house_p.needs_image or any(q.needs_image for _, q in scored)
    master_ps = [p for sc, p in scored if p is not house_p and sc < scored[0][0]]
    up = _page_text(house_p).upper()
    house = _after_label(up, BL_LABEL)
    mfrom_house = [n for n in _after_label(up, MBL_LABEL) if n not in house]
    if not house:
        house = [n for n in BL_TOKEN.findall(up[:1500]) if _bl_number(n) and n not in mfrom_house]
    labelled, loose = list(mfrom_house), []
    for p in scored[1:]:
        # 하우스가 아닌 다른 선하증권 쪽(마스터, 선적 안내문, 첨부)의 라벨 번호가 먼저고, 라벨 없는 번호꼴 토큰은 그 다음이다
        u = _page_text(p[1]).upper()
        labelled += [n for n in _after_label(u, MBL_LABEL) + _after_label(u, BL_LABEL) if n not in house]
        loose += [n for n in BL_TOKEN.findall(u[:600]) if _bl_number(n) and n not in house]
    master = labelled or loose
    master = [n for n in dict.fromkeys(master) if n not in house]
    return {"house": list(dict.fromkeys(house)), "master": master, "house_page": house_p, "ocr": from_ocr}


def _page_text(p):
    return p.ocr if p.needs_image else p.text


def _near(a, cands, tol=2):
    """OCR 은 O 와 0, I 와 1 을 헷갈린다. 그 둘을 맞춘 뒤 글자 두 개 안으로 다르면 같은 번호로 본다."""
    def n(x): return re.sub(r"[^A-Z0-9]", "", str(x).upper()).replace("O", "0").replace("I", "1")
    a = n(a)
    if not a: return False
    for c in cands:
        c = n(c)
        if abs(len(a) - len(c)) > tol: continue
        # 짧은 편집 거리
        prev = list(range(len(c) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cc in enumerate(c, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cc)))
            prev = cur
        if prev[-1] <= tol: return True
    return False


def sea_fix(ex, pages):
    """해상 꾸러미의 후처리. 운임 조건은 하우스 B/L 쪽(하우스 번호가 찍힌 쪽)에서 읽고,
    선명과 항차는 V. 와 / 를 뺀 한 줄로 맞춘다. 마스터 B/L 은 대개 PREPAID 라 모델이 그쪽을 집었다."""
    nums = sea_numbers(pages)
    if nums and nums.get("ocr"):
        # 스캔 B/L. OCR 번호는 틀린 글자가 섞일 수 있어 값으로 넣지 않는다. 모델이 두 번호의 자리를 바꿨을 때만 바로잡는다
        hv, mv = ex.get("hawb"), ex.get("mawb")
        hc, mc = nums["house"], nums["master"]
        exact_h = not nums["house_page"].needs_image       # 하우스 쪽이 글자층이면 그 번호는 정확하다
        if hv and _near(hv, mc) and not _near(hv, hc):
            # 모델이 마스터 번호를 하우스 자리에 적었다
            if mv and _near(mv, hc): ex["hawb"], ex["mawb"] = mv, hv
            elif hc: ex["hawb"], ex["mawb"] = hc[0], (mv or hv)
        if not ex.get("hawb") and hc: ex["hawb"] = hc[0]
        if exact_h and hc and not _near(ex.get("hawb"), hc[:1], tol=0): ex["hawb"] = hc[0]
        if not ex.get("mawb") and mc and not _near(mc[0], [ex.get("hawb")]):
            ex["mawb"] = mc[0]                          # 스캔 마스터 번호. 틀린 글자가 섞일 수 있어 검토 표시가 붙는다
        m2 = re.sub(r"[^A-Z0-9]", "", str(ex.get("mawb") or "").upper())
        for c in mc:
            if m2 != c and m2.startswith(c) and len(m2) - len(c) <= 3: ex["mawb"] = c   # 옆 글자가 붙은 번호(…1768HP)
        hp = nums["house_page"]
        n = ex.get("awb_pieces")
        if isinstance(n, (int, float)):
            m = re.search(r"\(?\s*%d\s*(PALLETS?|PLTS?|PACKAGES?|PKGS?|DRUMS?|CARTONS?|CTNS?|CASES?|RACKS?|CRATES?|BOXES|BOX|BAGS?|ROLLS?|SKIDS?|CYLINDERS?)\b" % int(n),
                          _page_text(hp).upper())
            if m: ex["package_unit"] = unit_code(m.group(1))
        u = _page_text(hp).upper()
        if "FREIGHT COLLECT" in u: ex["freight_term"] = "COLLECT"
        elif "FREIGHT PREPAID" in u: ex["freight_term"] = "PREPAID"
        nums = None
    if nums:
        hv = re.sub(r"[^A-Z0-9]", "", str(ex.get("hawb") or "").upper())
        mv = re.sub(r"[^A-Z0-9]", "", str(ex.get("mawb") or "").upper())
        if nums["house"] and hv not in nums["house"]:
            # 모델이 마스터를 하우스 자리에 적었거나 비웠다. 하우스 쪽의 B/L No. 를 쓴다
            if hv and hv in nums["master"] and not mv: ex["mawb"] = hv
            ex["hawb"] = nums["house"][0]
        if (not mv or mv in nums["house"]) and nums["master"]:
            ex["mawb"] = nums["master"][0]
        elif mv and mv not in nums["master"]:
            for c in nums["master"]:
                if mv.startswith(c) and len(mv) - len(c) <= 3: ex["mawb"] = c
        elif mv and mv == re.sub(r"[^A-Z0-9]", "", str(ex.get("hawb") or "").upper()):
            ex["mawb"] = None
        # 포장 단위. 하우스 쪽의 "(20 PALLETS)" 가 포장 수와 맞으면 그 낱말이다. 포장명세서의 낱개(80 DR)보다 앞선다
        n = ex.get("awb_pieces")
        if isinstance(n, (int, float)):
            m = re.search(r"\(?\s*%d\s*(PALLETS?|PLTS?|PACKAGES?|PKGS?|DRUMS?|CARTONS?|CTNS?|CASES?|RACKS?|CRATES?|BOXES|BOX|BAGS?|ROLLS?|SKIDS?|CYLINDERS?)\b" % int(n),
                          nums["house_page"].text.upper())
            if m: ex["package_unit"] = unit_code(m.group(1))
    h = re.sub(r"[^A-Z0-9]", "", str(ex.get("hawb") or "").upper())
    if h:
        for p in pages:
            if p.kind != "AWB" or p.needs_image: continue
            if h in re.sub(r"[^A-Z0-9]", "", p.text.upper()):
                u = p.text.upper()
                if "FREIGHT COLLECT" in u: ex["freight_term"] = "COLLECT"
                elif "FREIGHT PREPAID" in u: ex["freight_term"] = "PREPAID"
                break
    if isinstance(ex.get("flight1"), str):
        v = re.sub(r"\s*/\s*", " ", ex["flight1"])
        v = re.sub(r"\b(?:V[.\-:]|VOY(?:AGE)?[.\-:]?)\s*(?=\d)", "", v, flags=re.I)
        ex["flight1"] = re.sub(r"\s+", " ", v).strip(" -") or None
    ex["item_desc"] = clean_sea_desc(ex.get("item_desc"))
    if isinstance(ex.get("hawb"), str) and re.match(r"^0[A-Z]{2,}", ex["hawb"]):
        ex["hawb"] = "O" + ex["hawb"][1:]        # 스캔에서 글자 O 가 0 으로 읽힌 접두어(0 으로 시작하는 접두어)
    agent_rx = r"LOGISTICS|FORWARDING|SUPPLY CHAIN|SHIPPING|EXPRESS|NETWORKS|\bGLS\b|TRANSPORT"
    master_like = isinstance(ex.get("shipper_name"), str) and bool(re.search(r"\bO/B\b", ex["shipper_name"], re.I))
    if isinstance(ex.get("shipper_name"), str):
        m = re.search(r"\bO/B\s+(?:OF\s+)?(.+)$", ex["shipper_name"], re.I | re.S)
        if m: ex["shipper_name"] = m.group(1).strip(" ,") or ex["shipper_name"]   # "PNS NETWORKS O/B OF X" 는 마스터의 송하인이고 X 가 진짜 송하인이다
    hp = (nums or {}).get("house_page") if nums else None
    if hp is None:
        hv = re.sub(r"[^A-Z0-9]", "", str(ex.get("hawb") or "").upper())
        hp = next((p for p in pages if p.kind == "AWB" and hv and hv in re.sub(r"[^A-Z0-9]", "", _page_text(p).upper())), None)
    if hp is not None:
        ht = _page_text(hp)
        def block(label_rx):
            m = re.search(label_rx + r"[^\n]*\n\s*([^\n]{4,})", ht, re.I)
            if not m: return None
            v = re.split(r"\s{3,}", m.group(1).strip())[0].strip()
            return v if re.search(r"[A-Za-z]{3}", v) and not re.search(r"(?i)complete name|non-negotiable", v) else None
        # 마스터에서 이름을 가져온 흔적(O/B, 대리점 수하인)이 있으면 하우스 쪽 칸에서 다시 읽는다
        if master_like or (isinstance(ex.get("shipper_name"), str) and re.search(agent_rx, ex["shipper_name"].upper())
                           and not re.search(agent_rx, (block(r"(?:consignor\s*/\s*)?shipper(?:\s*/\s*exporter)?") or "").upper())):
            v = block(r"(?:consignor\s*/\s*)?shipper(?:\s*/\s*exporter)?")
            if v and not re.search(r"\bO/B\b", v): ex["shipper_name"] = v
        if isinstance(ex.get("consignee_name"), str) and re.search(agent_rx, ex["consignee_name"].upper()):
            v = block(r"consignee")
            if v and not re.search(agent_rx, v.upper()): ex["consignee_name"] = v
        # 라벨 없는 하우스 양식은 선명 줄의 오른쪽이 선적항이다. 모델이 두 항구를 뒤바꾼 경우를 바로잡는다
        vessel = str(ex.get("flight1") or "").split(" ")[0]
        if vessel and ex.get("origin") and ex.get("destination"):
            for line in ht.splitlines():
                if vessel.upper() in line.upper():
                    rest = line.upper().split(vessel.upper(), 1)[1]
                    d = re.sub(r"[^A-Z]", "", str(ex["destination"]).upper()); o = re.sub(r"[^A-Z]", "", str(ex["origin"]).upper())
                    r2 = re.sub(r"[^A-Z]", "", rest)
                    if d and d[:6] in r2 and o[:6] not in r2:
                        ex["origin"], ex["destination"] = ex["destination"], ex["origin"]
                    break
    return ex


NUM_KEYS = ("ci_total", "ci_gross", "ci_net", "pl_packages", "pl_gross", "pl_net", "awb_pieces", "awb_gross", "awb_chargeable",
            "awb_declared_value", "measurement_cbm", "awb_weight_charge", "awb_valuation_charge", "awb_tax",
            "awb_other_charges_agent", "awb_other_charges_carrier", "awb_total", "lca_total_issue", "lca_grand_total_usd")


def _to_num(v):
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    if not m: return None
    f = float(m.group(0).replace(",", ""))
    return int(f) if f == int(f) else f


# 서류 머리에 한 번만 찍히는 PO 번호. 「P/O NO. : 4353509671」, 「8. PO NO. & Date」 꼴이다.
HY_PO = re.compile(r"(?i)p\s*/?\s*o\.?\s*(?:no\.?|number)?\s*(?:&\s*date)?\s*[:.\s]\s*([0-9]{8,12})")


TEXT_CACHE_DIR = os.path.expanduser(os.environ.get("DOCPIPE_TEXT_CACHE")
                                    or wuxi_paths.work("out", "_textcache"))


def _cache_key(paths):
    import hashlib
    h = hashlib.sha1()
    for f in paths:
        try:
            st = os.stat(f)
            h.update(f"{os.path.basename(f)}|{st.st_size}|{int(st.st_mtime)}|".encode())
        except OSError:
            h.update(f"{f}|?|".encode())
    return h.hexdigest()[:20]


def doc_text_or_ocr(paths):
    """서류의 글. 글자층이 없으면 OCR 로 읽는다.

    🔴 왜 OCR 까지 가나. 전면 스캔 서류에서 P/O 가 화면상 네 쪽에 찍혀 있는데도 빈칸으로
       나왔다(전면 스캔 해상 표본). 글자층만 보면 그 서류는 「아무것도 안 적힌 서류」 다.
       하이닉스가 꼭 필요하다고 한 셋 중 하나를 글자층이 없다는 이유로 포기할 수 없다.
    """
    # 🔴 대조용 글자는 서류가 안 바뀌면 늘 같다. 화면을 만들 때와 엑셀을 만들 때 두 번
    #    OCR 하면 그만큼 기다린다. 파일 크기와 시각으로 열쇠를 만들어 한 번만 읽는다.
    key = _cache_key(paths)
    cf = os.path.join(TEXT_CACHE_DIR, key + ".txt")
    try:
        if os.path.isfile(cf):
            with open(cf, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        pass
    txt = ""
    try:
        import pymupdf
    except Exception:
        return txt
    for f in paths:
        try:
            d = pymupdf.open(f)
            txt += "".join(x.get_text() for x in d)
            d.close()
        except Exception:
            pass
    # 🔴 글자층이 없는 **쪽**은 언제나 OCR 한다. 앞 판은 서류 전체가 얇을 때만 OCR 해서,
    #    글자 인보이스에 스캔 통관서류가 섞인 묶음에서는 그 쪽의 글자를 못 봤다. 그 탓에
    #    「이 값이 서류에 있나」 를 묻는 검사가 스캔 쪽 값을 전부 「없다」 로 판정했다.
    # 🔴 글자가 조금 있어도 **표의 값이 그림인** 쪽이 있다. 양식 낱말만 글자층에 있고 값은
    #    그림으로 찍힌다. 실측(품목 3줄 우시 표본)에서 쪽마다 1,000자 가까운 글자가 있었는데
    #    Material No 열의 값 P1120301 은 글자층에 없고 그림에만 있었다. 그 탓에 「원문에 없다」
    #    로 판정해 멀쩡한 코드를 지어낸 값으로 몰 뻔했다.
    #    그래서 글자가 거의 없는 쪽뿐 아니라 그림이 박힌 쪽도 함께 OCR 한다.
    try:
        pages = [pg for f in paths for pg in load_pdf(f)]
        want = []
        for pg in pages:
            thin = len((pg.text or "").strip()) < 40
            if thin or getattr(pg, "n_images", 0):
                want.append(pg)
        want = want[:12]                      # 쪽이 많은 묶음에서 시간이 튀지 않게 막는다
        if want:
            for pg in want:
                classify(pg)
                # 🔴 ocr_pages 는 SCAN 으로 갈린 쪽만 읽는다. 여기서 모으는 글자는 화면에
                #    보여 줄 것이 아니라 「이 값이 서류에 있나」 를 묻는 대조용이므로, 읽고
                #    싶은 쪽을 그때만 SCAN 으로 표시한다. 이 Page 객체는 이 함수 안에서만 산다.
                pg.kind = "SCAN"
            d = ocr_pages(want)
            try:
                txt += "\n" + "\n".join(getattr(pg, "ocr", "") or "" for pg in want)
            finally:
                if d:
                    shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
    try:
        os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
        with open(cf, "w", encoding="utf-8") as fh:
            fh.write(txt)
    except OSError:
        pass
    return txt


CODE_KEYS = ("sap_code", "material_code", "part_no", "iv_no", "hios_no", "serial_no", "po_item_no")


def _flat(t):
    return re.sub(r"[^A-Z0-9]", "", (t or "").upper())


def doc_has_scan(paths):
    """글자층이 없는 쪽이 하나라도 있나. 원문 대조의 잣대를 여기서 가른다."""
    try:
        for f in paths:
            for pg in load_pdf(f):
                if len((pg.text or "").strip()) < 40:
                    return True
    except Exception:
        return True          # 못 열면 모르는 것이다. 모르면 지우지 않는다
    return False


def hynix_drop_unfounded(ex, full_text, has_scan=True):
    """서류에 없는 품목 코드를 지운다. 몇 개를 지웠는지 돌려준다.

    🔴 왜 지우나. 실측(품목 3줄 우시 표본)에서 P1120301, P3163712, P3163711 이 나왔는데 서류의
       글자층에도 OCR 에도 그 값이 없었다. 모델이 지어낸 값이다. 그 칸은 하이닉스가 GERP 의
       Material 로 쓰는 자리다. 빈칸이면 사람이 채우지만, 지어낸 값은 그대로 ERP 로 들어간다.
       빈칸보다 나쁘다.

    🔴 왜 PO 는 검사가 있었는데 코드는 없었나. 줄 단위 검증을 만들 때 PO 만 원문 대조를
       걸었다. 코드도 같은 자를 대야 한다는 것을 이 건에서 알았다.
    """
    flat = _flat(full_text)
    if len(flat) < 200:
        return 0          # 읽을 글자가 없으면 판단하지 않는다. 지우면 멀쩡한 값이 날아간다
    n = 0
    for lk in ("ci_lines", "pl_lines"):
        for ln in (ex.get(lk) or []):
            if not isinstance(ln, dict):
                continue
            for k in CODE_KEYS:
                v = str(ln.get(k) or "").strip()
                if not v or len(_flat(v)) < 4:
                    continue
                if _flat(v) in flat:
                    continue
                # 🔴 판정의 잣대는 「스캔이냐」 가 아니라 「어디서 온 값이냐」 다.
                #    통관 용도설명서에서 가져온 값은 출처가 분명하다. 그 표는 그림으로 인쇄돼
                #    글자층에도 OCR 에도 코드가 안 남지만, 우리가 어느 표에서 가져왔는지 안다.
                #    지우면 하이닉스가 제일 필요하다고 한 Material 이 통째로 날아간다.
                #    반면 출처 표시가 없는데 원문에도 없는 값은 지어낸 값이다.
                # 🔴 전면 스캔 쪽이 있는 묶음에서는 지우지 않는다. 우리 OCR 이 못 읽는 글씨를
                #    모델은 그림에서 읽는다. 지우면 그 건의 품목 코드가 통째로 날아간다.
                #    글자층이 온전한 서류에서만 「원문에 없으면 지어낸 값」 이라고 본다.
                if has_scan or (k == "sap_code" and ln.get("_sap_from")):
                    ln.setdefault("_unverified", []).append(f"{k}={v} ({ln['_sap_from']})")
                else:
                    ln[k] = None
                    ln.setdefault("_dropped", []).append(f"{k}={v}")
                    n += 1
    return n


HY_ORIGIN = re.compile(r"(?i)(?:country\s*of\s*origin|원산지)\s*[:：]\s*([A-Za-z][A-Za-z.\' ]{1,22}?)(?=\s{2,}|[\n,;]|$)")
# 🔴 「Country of Origin :」 뒤에 값이 안 붙고 다른 줄에 「Made in Korea」 로 적히는 서류가 있다.
#    스캔을 OCR 로 읽으면 라벨과 값이 아예 다른 줄로 떨어진다. 그 꼴도 같이 본다.
HY_ORIGIN2 = re.compile(r"(?i)\bmade\s+in\s+([A-Za-z][A-Za-z.\' ]{1,20}?)(?=\s{2,}|[\n,;.)]|$)")


def hynix_fill(ex, full_text, has_scan=True):
    """서류 전체에 한 번만 찍힌 값을 줄에 채우고, 근거 없는 코드를 지운다.

    🔴 왜 필요한가. ’26.09.30 하이닉스 시험 서류 7건에서 두 가지가 드러났다.

    1. **원산지가 줄마다 안 찍히고 서류에 한 번만 찍힌다.** 「Country of Origin : KOREA」 가
       머리에 한 줄 있고 표에는 없다. 프롬프트는 그럴 때 비우라고 했는데, 고객이 요구한
       열 개 중 하나가 COUNTRY OF ORIGIN 이다. 한 번만 찍혔으면 그 선적 전체가 그 원산지다.
       비워서 「안 됩니다」 라고 말할 자리가 아니다.

    2. **Sap Code 열이 없는 서류인데 모델이 sap_code 를 채웠다.** 서류 어디에도 sap 라는
       글자가 없는데 값이 들어왔다. 빈칸보다 나쁘다. 받는 쪽이 그 값을 SAP 코드로 믿는다.
       글자층이 있는 서류에서 근거가 없으면 지운다. 스캔은 글자가 없으니 손대지 않는다.
    """
    if not isinstance(ex, dict):
        return ex
    lines = [x for k in ("ci_lines", "pl_lines") for x in (ex.get(k) or []) if isinstance(x, dict)]
    if not lines:
        return ex
    has_text = len((full_text or "").strip()) > 200
    # 🔴 순서가 중요하다. 근거 없는 SAP 코드를 **먼저** 지우고 그 다음에 통관서류에서 채운다.
    #    거꾸로 하면 인보이스에서 잘못 읽은 코드가 자리를 차지해 통관서류의 맞는 코드가 안 들어간다.
    #    실측(품목 17줄 우시 표본)에서 인보이스에는 SAP 열이 없는데 P3187097 이 들어와 있었고,
    #    같은 묶음 용도설명서의 맞는 코드는 F0181F001 이었다.
    if has_text and not re.search(r"(?i)sap", full_text or ""):
        for ln in lines:
            ln["sap_code"] = None
    hynix_merge_cn(ex)
    # 🔴 줄에 PO 가 하나도 없고 서류에 PO 번호가 딱 하나만 찍혀 있으면 그 선적 전체가 그 PO 다.
    #    표에 PO 열이 없고 머리에 한 줄만 있는 서류가 실제로 있다. 비워 두면 GERP 에서
    #    그 줄들을 사람이 전부 손으로 잇는다.
    if not any(str(ln.get("po_no") or "").strip() for ln in lines):
        cand = []
        for m0 in HY_PO.finditer(full_text or ""):
            v = m0.group(1)
            if v not in cand:
                cand.append(v)
        if len(cand) == 1:
            for ln in lines:
                ln["po_no"] = cand[0]
                ln["_po_from"] = "서류 머리"
    hynix_drop_unfounded(ex, full_text, has_scan)
    # 🔴 하이닉스 품목 코드가 part_no 자리에 들어오는 서류가 있다. 인보이스 머리글이
    #    「Material No」 인데 모델이 공급사 품번으로 본 것이다(품목 3줄 우시 표본: P1120301).
    #    그 값은 서류에 실제로 찍혀 있고 꼴도 다른 서류의 Sap Code, Material No 와 같다.
    #    비워 두면 하이닉스가 제일 필요하다고 한 Material 이 그 건에서 통째로 빈다.
    #    공급사 모델명(M308-004-24A 처럼 줄표가 든 것)은 이 꼴에 안 걸리므로 안 옮긴다.
    hy_like = re.compile(r"^[A-Z]\d{6,8}$")
    for ln in lines:
        if str(ln.get("sap_code") or "").strip() or str(ln.get("material_code") or "").strip():
            continue
        v = str(ln.get("part_no") or "").strip()
        if v and hy_like.match(v):
            ln["material_code"] = v
            ln["_mat_from"] = "part_no 자리"
    m = HY_ORIGIN.search(full_text or "") or HY_ORIGIN2.search(full_text or "")
    if m:
        one = m.group(1).strip().rstrip(".,").upper()
        for ln in lines:
            if not str(ln.get("country_of_origin") or "").strip():
                ln["country_of_origin"] = one
    return ex


def _norm_desc(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def hynix_merge_cn(ex):
    """통관 용도설명서의 품목 코드를 인보이스 줄에 채운다. 채운 줄에는 출처를 남긴다.

    🔴 왜 필요한가. 하이닉스가 꼭 달라고 한 셋 중 하나가 Material 인데, 인보이스에 그 열이
       인쇄되지 않은 서류가 절반이다. 같은 묶음의 용도설명서에는 품목마다 SAP 코드가 있다.
       버리던 쪽에 답이 있었다.

    🔴 어떻게 잇나. 품명으로 먼저 잇고, 안 되면 줄 수가 같을 때만 인쇄 순서로 잇는다.
       줄 수가 다른데 순서로 이으면 엉뚱한 코드가 붙는다. 그건 빈칸보다 나쁘다.

    🔴 출처를 남기는 이유. 인보이스에서 읽은 값과 통관서류에서 가져온 값은 근거가 다르다.
       화면과 표가 그 둘을 같은 얼굴로 보여 주면 받는 쪽이 검증할 방법이 없다.
    """
    cn = [x for x in (ex.get("cn_lines") or []) if isinstance(x, dict)]
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    if not cn or not ci:
        return ex
    by_desc = {}
    for c in cn:
        for key in (_norm_desc(c.get("desc_en")), _norm_desc(c.get("model"))):
            if len(key) >= 5:
                by_desc.setdefault(key, c)
    same_len = len(cn) == len(ci)
    for i, ln in enumerate(ci):
        # 🔴 통관 용도설명서를 SAP Code 와 원산지의 기준으로 삼는다. 그 표의 열은 「SKhynix SAP Code」
        #    라고 이름이 붙어 있고, 인보이스에서 읽은 값은 열 이름이 없는 자리에서 나온
        #    경우가 있다. 실측에서 인보이스 값이 P3187097 이고 용도설명서 값이 F0181F001
        #    이었다. 앞 판은 인보이스 값이 있으면 넘어가서 틀린 값이 그대로 남았다.
        d = _norm_desc(ln.get("description"))
        hit = by_desc.get(d)
        if hit is None and len(d) >= 8:
            # 🔴 앞 8자가 여러 줄에 걸리면 고르지 않는다(회사 docagent hx_item.catalog_name 의 원칙, 10/29 이관 때 합침).
            #    앞 판은 처음 걸린 줄을 붙여서, 품명 앞부분이 같은 품목이 여럿이면 남의 코드가 붙었다.
            #    여럿 걸리면 줄 수가 같을 때 같은 순번의 줄이 그 후보 안에 있을 때만 잇는다.
            cands = []
            for k, v in by_desc.items():
                if (k.startswith(d[:8]) or d.startswith(k[:8])) and all(v is not c for c in cands):
                    cands.append(v)
            if len(cands) == 1:
                hit = cands[0]
            elif cands:
                if same_len and any(cn[i] is c for c in cands):
                    hit = cn[i]
                else:
                    continue
        if hit is None and same_len:
            hit = cn[i]
        if hit is None:
            continue
        # 🔴 코드와 원산지를 따로 본다. 앞 판은 코드가 없으면 그 줄을 통째로 건너뛰어서
        #    원산지까지 같이 잃었다. 통관서류의 코드 읽기가 실패한 회차에 원산지가 100% 에서
        #    77% 로 떨어졌다. 두 값은 서로 다른 열이고 한쪽이 안 읽혔다고 다른 쪽을 버릴
        #    이유가 없다.
        code = str(hit.get("sap_code") or "").strip()
        if code:
            old = str(ln.get("sap_code") or "").strip()
            if old and old != code:
                ln["_sap_was"] = old    # 바뀐 값을 남긴다. 사람이 둘을 견줄 수 있어야 한다
            ln["sap_code"] = code
            ln["_sap_from"] = "통관서류"
        if not str(ln.get("country_of_origin") or "").strip() and hit.get("country_of_origin"):
            ln["country_of_origin"] = hit["country_of_origin"]
            ln["_org_from"] = "통관서류"
    return ex


def _line_str_keys(list_key):
    """이 배열의 줄 항목에서 스키마가 글자라고 적은 칸 이름."""
    it = ((SCHEMA.get("properties") or {}).get(list_key) or {}).get("items") or {}
    out = {"description", "code"}          # 스키마를 못 읽는 길에서도 예전과 같이 돈다
    for k, v in (it.get("properties") or {}).items():
        t = v.get("type") if isinstance(v, dict) else None
        if t == "string" or (isinstance(t, list) and "string" in t):
            out.add(k)
    return out


def _dedupe_h1(ex):
    """하이닉스 판의 건 단위 배열 둘에서 겹친 것을 뺀다.

    서류별로 나눠 부르면 인보이스 네 장이 네 번 답하고, 같은 PO 가 여러 장에 찍혀 있으면
    합칠 때 그만큼 되풀이된다. 세는 자리(검증, 표)가 다 이 배열을 쓰므로 여기서 한 번만
    정리한다. 순서는 처음 나온 차례를 지킨다.
    """
    if isinstance(ex.get("po_list"), list):
        seen, out = set(), []
        for x in ex["po_list"]:
            v = str(x or "").strip()
            if v and v not in seen:
                seen.add(v); out.append(v)
        ex["po_list"] = out
    if isinstance(ex.get("invoice_totals"), list):
        seen, out = set(), []
        for x in ex["invoice_totals"]:
            if not isinstance(x, dict): continue
            k = str(x.get("invoice_no") or "").strip()
            if k and k in seen: continue
            if k: seen.add(k)
            out.append(x)
        ex["invoice_totals"] = out
    return ex


def price_twins(lines, total, qty="qty", amount="amount", codes=_CODE_PAIR):
    """한 인보이스의 줄 목록이 같은 줄을 금액만 다르게 두 번 담았으면, 총액에 안 맞는 쪽 줄 번호를 낸다. 아니면 빈 목록.

    수리 인보이스는 같은 품목을 「No Commercial Value」 값과 「Repair Charge」 값으로 두 번 찍는다(PNS26081814: 27줄씩,
    합 117,913.71 과 44,189.20, 총액 44,189.20). 앞뒤 반(표 둘)이나 번갈아(줄마다 두 값) 담긴다.
    🔴 두 쪽이 차례마다 코드와 수량이 같아야 하고(같은 줄), 한쪽 금액 합만 총액과 맞아야 뺀다. 그렇지 않으면 아무것도
       빼지 않는다(서로 다른 줄일 수 있음). 줄 금액이 하나라도 비면 따지지 않는다.
    """
    n = len(lines)
    if n < 2 or n % 2 or not total:
        return []
    amts = [num(l.get(amount)) for l in lines]
    if any(a is None for a in amts) or close(sum(amts), total):
        return []

    def twin(a, b):
        """0 같은 줄, 1 수량은 같고 코드만 다름, 2 다른 줄."""
        if num(a.get(qty)) != num(b.get(qty)):
            return 2
        ca = {_norm_code(a.get(k)) for k in codes if a.get(k) not in (None, "")}
        cb = {_norm_code(b.get(k)) for k in codes if b.get(k) not in (None, "")}
        return 0 if (ca & cb if ca and cb else not ca and not cb) else 1

    h = n // 2
    for first, second in ((list(range(h)), list(range(h, n))), (list(range(0, n, 2)), list(range(1, n, 2)))):
        t = [twin(lines[i], lines[j]) for i, j in zip(first, second)]
        # 스캔 사본은 코드 한 글자를 잘못 읽는다(PNS26081814 의 MMF07202 와 MMF07366). 세 줄 넘는 목록에서 한 줄까지 봐준다
        if 2 in t or t.count(1) > (1 if h >= 3 else 0):
            continue
        s1, s2 = sum(amts[i] for i in first), sum(amts[i] for i in second)
        if close(s1, total) and not close(s2, total):
            return second
        if close(s2, total) and not close(s1, total):
            return first
        # 어느 쪽도 총액과 꼭 맞지 않을 때(판독이 Total Amount 를 총액으로 받고 줄 하나를 조금 다르게 읽음,
        # PNS26081814 는 39,102.91 과 123,000, 총액 44,189.20). 한쪽만 총액을 넘으면 넘는 쪽은 그 인보이스의 줄일 수
        # 없다. 남은 쪽도 총액과 안 맞으므로 빠짐 검사(금액 합)는 그대로 「빠진 줄 의심」을 띄운다
        over = 1.01 * total
        if s2 > over >= s1:
            return second
        if s1 > over >= s2:
            return first
    return []


def postprocess(ex, customer=None):
    """모델 출력을 verify() 가 읽는 꼴로 다듬는다. 값을 바꾸지 않고 꼴만 바꾼다."""
    ex = _dedupe_h1(dict(ex))
    # 모델이 인자를 {"input": {...}} 처럼 한 겹 더 싸서 주는 경우가 있다 (Opus 5 에서 한 케이스). 벗긴다
    if len(ex) == 1:   # 키 이름은 input, args, extract 등 제각각이다
        inner = next(iter(ex.values()))
        if isinstance(inner, dict) and len(set(inner) & set(SCHEMA["properties"])) >= 3: ex = dict(inner)
    for k, v in list(ex.items()):
        if isinstance(v, str) and "&" in v:
            import html
            ex[k] = v = html.unescape(v)        # 모델이 &amp; 로 적는 일이 있다
        # 널을 못 주는 스키마(Upstage)는 빈 문자열이나 0 을 준다. 이 서류에서 중량, 금액, 수량이 0 인 값은 없다
        if v == "" or (isinstance(v, (int, float)) and v == 0 and not isinstance(v, bool)): ex[k] = None
        # 값이 없을 때 널 대신 낱말을 적는 모델이 있다. 스캔 서류를 글자로만 보낼 때 특히 그렇다
        elif isinstance(v, str) and re.sub(r"[^A-Z]", "", v.upper()) in NULLISH: ex[k] = None
    if isinstance(ex.get("currency"), str) and re.search(r"YEN|円", ex["currency"].upper()): ex["currency"] = "JPY"
    for k in NUM_KEYS:
        v = ex.get(k)
        if isinstance(v, str):
            m = re.search(r"-?\d[\d,]*(?:\.\d+)?", v)
            ex[k] = float(m.group(0).replace(",", "")) if m else None
            if ex[k] is not None and ex[k] == int(ex[k]): ex[k] = int(ex[k])
    # 🔴 줄 안의 글자를 숫자로 바꾸되, 스키마가 글자라고 적은 칸은 건드리지 않는다.
    #    예전에는 예외가 ("description", "code") 두 이름이었다. 하이닉스 판이 줄에 글자 칸을
    #    여덟 개 더 얹으면서 그 이름 목록이 그대로 사고가 됐다. 인보이스 번호
    #    "2026-01-MSP_WUXI" 가 2026 이 되고 Sap Code "P3103668" 이 3103668 이 됐다.
    #    이름을 적어 두는 대신 스키마에 묻는다. 예전 판에서 글자 칸은 정확히 그 둘이라
    #    하던 대로 돈다.
    for k in ("ci_lines", "pl_lines", "lca_lines", "dims_cm"):
        if isinstance(ex.get(k), list):
            keep = _line_str_keys(k)
            fixed = []
            for ln in ex[k]:
                if isinstance(ln, dict):
                    ln = {a: (_to_num(b) if a not in keep and isinstance(b, str) else b) for a, b in ln.items()}
                fixed.append(ln)
            ex[k] = fixed
    if isinstance(ex.get("freight_term"), str):
        u = ex["freight_term"].upper()
        ex["freight_term"] = "COLLECT" if "COLLECT" in u else "PREPAID" if "PREPAID" in u or "PPD" in u else ex["freight_term"]
    if isinstance(ex.get("customer_po"), str) and not re.search(r"\d", ex["customer_po"]):
        ex["customer_po"] = None          # "See following", "NO SK HYNIX PO" 같은 말은 번호가 아니다
    # 🔴 줄 단위 PO 와 PO 목록에도 같은 자를 댄다. 건 단위 칸에만 걸어 두고 줄에는 안 걸었더니
    #    운송장의 신고가액 없음 표시(N.C.V)가 줄의 PO 번호로 들어왔다. 실측에서 그 값 하나가
    #    PO 목록까지 올라가 검증 두 줄을 같이 틀리게 만들었다. 숫자가 없는 것은 번호가 아니다.
    for lk in ("ci_lines", "pl_lines"):
        for ln in (ex.get(lk) or []):
            if isinstance(ln, dict) and isinstance(ln.get("po_no"), str) and not re.search(r"\d", ln["po_no"]):
                ln["po_no"] = None
    if isinstance(ex.get("po_list"), list):
        ex["po_list"] = [x for x in ex["po_list"] if re.search(r"\d", str(x or ""))]
    for k in ("ci_lines", "pl_lines", "lca_lines"):
        if ex.get(k): ex[k] = [ln for ln in ex[k] if any(isinstance(x, (int, float)) and x for x in ln.values())]
    if ex.get("dims_cm") and isinstance(ex["dims_cm"][0], dict):
        # 🔴 치수 칸은 숫자로만 남긴다. 모델이 "120 x 80" 이나 "N/A" 같은 글자를 l 에 넣으면 위의 숫자
        #    바꾸기가 못 바꾸고 글자가 남았고, verify 의 곱셈이 TypeError 로 죽어 그 묶음 전체가
        #    「오류」로 끝났다(실제 서류 점검 11/6, 스캔 3건). 숫자가 안 되는 치수는 버린다.
        rows = []
        for d in ex["dims_cm"]:
            if not isinstance(d, dict):
                continue
            l, w, h, n = (num(d.get(k)) for k in ("l", "w", "h", "n"))
            if l:
                rows.append([l, w or 0, h or 0, n or 1])
        ex["dims_cm"] = rows
    # 수리 인보이스는 같은 줄이 두 표(원래 값, 수리 값)에 찍힌다. 모델이 둘 다 담으면 총액에 맞는 쪽만 남긴다.
    lines, tot = ex.get("ci_lines") or [], num(ex.get("ci_total"))
    if tot and len(lines) >= 2 and len(lines) % 2 == 0:
        amts = [num(l.get("amount")) if isinstance(l, dict) else None for l in lines]
        if all(a is not None for a in amts) and not close(sum(amts), tot):
            h = len(lines) // 2
            for part in (lines[h:], lines[:h]):
                if close(sum(num(l.get("amount")) for l in part), tot):
                    ex["ci_lines"] = part; ex["ci_lines_dropped"] = h; break
    # 하이닉스 판은 총액을 인보이스마다 invoice_totals 로 받는다. 같은 규칙을 인보이스별로 건다(PNS26081814, 11/12)
    tots = {_norm_code(t.get("invoice_no")): num(t.get("total")) for t in ex.get("invoice_totals") or []
            if isinstance(t, dict) and t.get("invoice_no") and num(t.get("total"))}
    if tots and ex.get("ci_lines"):
        drop = set()
        for inv, tot in tots.items():
            idx = [i for i, l in enumerate(ex["ci_lines"]) if isinstance(l, dict)
                   and (_norm_code(l.get("invoice_no")) == inv or (len(tots) == 1 and not l.get("invoice_no")))]
            drop |= {idx[i] for i in price_twins([ex["ci_lines"][i] for i in idx], tot)}
        if drop:
            ex["ci_lines"] = [l for i, l in enumerate(ex["ci_lines"]) if i not in drop]
            ex["ci_lines_dropped"] = (ex.get("ci_lines_dropped") or 0) + len(drop)
    lca = ex.get("lca_lines") or []
    fixed = 0
    for ln in lca:   # "54.540" 처럼 점이 천 단위 구분자인 값. 환율 곱으로 알아내 1000배로 고친다
        ia, fx, ua = num(ln.get("issue_amt")), num(ln.get("exrate")), num(ln.get("usd_amt"))
        if ia and fx and ua and not close(ia * fx, ua, tol=0.02) and close(ia * 1000 * fx, ua, tol=0.02):
            ln["issue_amt"] = ia * 1000; fixed += 1
    if fixed and lca:
        ti = num(ex.get("lca_total_issue")); s_iss = sum(num(x.get("issue_amt")) or 0 for x in lca)
        if ti is not None and not close(s_iss, ti) and close(s_iss, ti * 1000): ex["lca_total_issue"] = ti * 1000
    # 출발지와 도착지: NRT-PVG 처럼 노선으로 적으면 앞과 뒤를 가르고, HKG HONG KONG 처럼 코드가 이름 앞에
    # 붙으면 이름만 남긴다. 회차마다 붙었다 떨어졌다 하는 자리다.
    for k, pick in (("origin", 0), ("destination", -1)):
        v = ex.get(k)
        if not isinstance(v, str): continue
        t = v.strip()
        m = re.fullmatch(r"([A-Z]{3})\s*-\s*([A-Z]{3})", t)
        if m: t = m.group(1) if pick == 0 else m.group(2)
        t = re.sub(r"^[A-Z]{3}\s+(?=[A-Z]{4,})", "", t)
        ex[k] = t or None
    # 통화는 세 글자 코드만 남긴다. US $, US$, USD. 처럼 찍힌다.
    if isinstance(ex.get("currency"), str):
        c = ex["currency"].upper()
        m = re.search(r"\b(USD|EUR|JPY|KRW|CNY|RMB|GBP|TWD|SGD|HKD|MYR|THB|VND|INR|AUD|CAD|CHF|PHP|IDR)\b", c)
        if m: ex["currency"] = "CNY" if m.group(1) == "RMB" else m.group(1)
        elif re.search(r"US\s*\$|^\$$", c): ex["currency"] = "USD"
        elif "₩" in c or c in ("WON", "KRW"): ex["currency"] = "KRW"
        elif "€" in c: ex["currency"] = "EUR"
        elif c.strip() in ("¥", "￥", "JPY¥", "¥JPY"): ex["currency"] = "JPY"   # 엔 기호만 찍는 일본계 인보이스
    # PO 번호 뒤에 붙는 설명은 뗀다. 4353374643 (YHS-140 108 GAL) 처럼 찍힌다.
    if isinstance(ex.get("customer_po"), str):
        po = re.sub(r"\s*\(.*$", "", ex["customer_po"])
        po = re.split(r"\s*[,&/;]\s*|\s+and\s+", po)[0]     # 여러 개면 첫 번째. KEROL 은 하나만 받는다
        ex["customer_po"] = po.strip() or None
    # 포장 수 자리에 낱개 수량을 적는 일이 잦다. 줄 수량의 합과 같고 AWB 피스와 다르면 AWB 피스를 쓴다.
    pk, pcs = num(ex.get("pl_packages")), num(ex.get("awb_pieces"))
    qsum = sum(num(x.get("qty")) or 0 for x in (ex.get("pl_lines") or []) if isinstance(x, dict))
    if pk is not None and pcs is not None and pk != pcs and qsum and close(pk, qsum) and pcs < pk:
        ex["pl_packages_raw"] = ex["pl_packages"]
        ex["pl_packages"] = ex["awb_pieces"]
    # 인코텀즈는 세 글자 코드만 남긴다. F.O.B KOREA, EXW, WUXI SKHYCL 처럼 찍힌다.
    if isinstance(ex.get("incoterms"), str):
        m = re.search(r"(?:^|\b)(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP|DDU|DAF|DES|DEQ|DAT)",
                      ex["incoterms"].replace(".", "").upper())
        ex["incoterms"] = m.group(1) if m else ex["incoterms"]
    # 품명 앞의 "4 PKG OF" 같은 포장 수 머리를 뗀다. 회차마다 붙었다 떨어졌다 한다.
    if isinstance(ex.get("item_desc"), str):
        ex["item_desc"] = clean_sea_desc(ex["item_desc"]) or ex["item_desc"]
    if isinstance(ex.get("item_desc"), str):
        ex["item_desc"] = re.sub(r"^\s*\d+\s*(?:PKGS?|PACKAGES?|CTNS?|CARTONS?|PLTS?|PALLETS?|BOX(?:ES)?|CASES?|SLAC|PCS?|PIECES?|SKIDS?|CRATES?|CT|GALS?|GALLONS?|LTRS?|KGS?|DRUMS?|BOTTLES?|BAGS?|ROLLS?|SETS?|UNITS?|EA|WOODEN(?: BOX(?:ES)?| CASES?)?|PLYWOOD|CYLINDERS?)\.?\s+OF\s+",
                                 "", ex["item_desc"], flags=re.I).strip() or None
    # 하우스 자리에 마스터 번호(항공사 3자리 + 8자리, 검증 숫자 맞음)를 적는 일이 있다. 옮긴다.
    def _mawb_digits(v):
        d = re.sub(r"\D", "", str(v or ""))
        return d if len(d) == 11 and int(d[3:10]) % 7 == int(d[10]) else None
    if ex.get("hawb") and _mawb_digits(ex["hawb"]) and re.match(r"^\s*\d{3}[\s\-]", str(ex["hawb"])):
        if not _mawb_digits(ex.get("mawb")):
            d = _mawb_digits(ex["hawb"]); ex["mawb"] = d[:3] + "-" + d[3:]
        ex["hawb"] = None
    if isinstance(ex.get("mawb"), str) and _mawb_digits(ex["mawb"]) and not re.match(r"^\s*[A-Z]{4}", ex["mawb"].upper()):
        # 선사 코드로 시작하는 해상 마스터 B/L(선사 코드 네 글자 + 숫자)은 항공 꼴로 바꾸지 않는다
        d = _mawb_digits(ex["mawb"]); ex["mawb"] = d[:3] + "-" + d[3:]    # 180 ICN 5410 2893 -> 180-54102893
    # 하우스 번호는 찍힌 대로 받아 여기서 공백을 뺀다. 모델에게 빼라고 하면 글자나 끝자리를 같이 떨군다.
    if isinstance(ex.get("hawb"), str):
        ex["hawb"] = re.sub(r"\s+", "", ex["hawb"])
    pu = ex.get("package_unit")
    if pu:
        ex["package_unit_raw"] = pu
        code = unit_code(pu, customer=customer)
        # C/No, C710 처럼 칸 이름이나 번호가 오면 단위가 아니다. 비워서 사람이 채운다.
        ex["package_unit"] = code if re.fullmatch(r"[A-Z][A-Z.]{0,5}", str(code)) else None
    return ex

def compare(ex, truth):
    """정답 JSON 과 항목별로 견준다. 숫자는 근사, 문자열은 공백과 하이픈을 빼고 대소문자 없이."""
    def tnorm(v):
        # 글자로만 견준다. 공백과 쉼표와 하이픈을 빼고 대소문자를 없앤다.
        return re.sub(r"[\s,\-]", "", str(v).replace(" inch", '"').replace("inch", '"')).lower()
    def norm(v):
        if isinstance(v, (int, float)): return round(float(v), 2)
        if v is None: return None
        n = num(v)
        if n is not None and re.fullmatch(r"[\d.,]+", str(v).strip()): return round(n, 2)
        return tnorm(v)
    rows = []
    for k, tv in truth.items():
        mv = ex.get(k)
        if isinstance(tv, list) and not tv and not mv:
            rows.append((k, True, "빈 값")); continue
        if k.endswith("_name") and isinstance(tv, str) and isinstance(mv, str):
            # 회사 이름은 마침표와 괄호가 회차마다 다르게 붙는다. 글자와 숫자만 견준다.
            ok = re.sub(r"[^a-z0-9]", "", mv.lower()) == re.sub(r"[^a-z0-9]", "", tv.lower())
            rows.append((k, ok, f"{tv}" if ok else f"정답 {tv} / 모델 {mv}")); continue
        if isinstance(tv, list):
            if k == "dims_cm":
                ok = mv is not None and [[float(x) for x in d] for d in mv] == [[float(x) for x in d] for d in tv]
            else:
                ok = mv is not None and len(mv) == len(tv) and all(
                    all(norm(a.get(f)) == norm(b.get(f)) for f in b if f != "description") for a, b in zip(mv, tv))
            rows.append((k, ok, f"{len(tv)}줄" if ok else f"정답 {tv} / 모델 {mv}"))
        elif k == "item_desc" and isinstance(tv, str) and isinstance(mv, str):
            # 품명은 첫 줄만 적기도 하고 설명 줄을 다 잇기도 한다. 한쪽이 다른 쪽을 품으면 같다고 본다.
            # 다만 상용구(SAID TO CONTAIN, SHIPPER'S LOAD)가 남아 있으면 틀린 것이다.
            # 품번처럼 숫자만 있는 품명도 있으므로 숫자로 바꾸지 않고 글자로만 견준다.
            a, b = tnorm(mv), tnorm(tv)
            ok = (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))) \
                and not re.search(r"saidto|shipper.?sload", a)
            rows.append((k, ok, f"{tv}" if ok else f"정답 {tv} / 모델 {mv}"))
        elif k == "flight1" and isinstance(tv, str) and isinstance(mv, str):
            # 선명과 항차. V. 와 / 는 안 세고, 정답의 낱말이 모델 답에 다 있으면 같다(SHUN DA 2608W 와 SHUN DA V.2608W)
            def toks(x): return set(t for t in re.sub(r"\bv[.\-]?(?=\d)", "", x.lower().replace("/", " ")).split() if t)
            ok = bool(toks(tv)) and toks(tv) <= toks(mv)
            rows.append((k, ok, f"{tv}" if ok else f"정답 {tv} / 모델 {mv}"))
        else:
            a, b = norm(mv), norm(tv)
            ok = a == b
            # 한쪽은 숫자, 한쪽은 숫자를 적은 글자인 경우. "20 7667 9570" 과 2076679570 은 같은 값이다
            if not ok:
                fa = num(a) if isinstance(a, str) else a
                fb = num(b) if isinstance(b, str) else b
                ok = isinstance(fa, float) and isinstance(fb, float) and round(fa, 2) == round(fb, 2)
            # 출발지와 도착지는 "TAIPEI" 와 "TAIPEI, TAIWAN, CHINA" 처럼 길이만 다른 경우가 많다
            if not ok and k in ("origin", "destination") and isinstance(a, str) and isinstance(b, str) and min(len(a), len(b)) >= 3:
                # 같은 항구의 옛 표기(INCHON, PUSAN)는 같은 코드로 간다
                def port(x): return x.replace("inchon", "incheon").replace("pusan", "busan")
                a2, b2 = port(a), port(b)
                ok = a2 in b2 or b2 in a2
            rows.append((k, ok, f"{tv}" if ok else f"정답 {tv} / 모델 {mv}"))
    return rows

# ---------- 4. 규칙 검증 ----------
def num(x):
    if x is None: return None
    if isinstance(x, (int, float)): return float(x)
    s = str(x).strip().replace(",", "")
    # "9.200.76" 같은 오타: 점이 둘이면 앞 점은 천 단위로 본다
    if s.count(".") > 1:
        head, tail = s.rsplit(".", 1); s = head.replace(".", "") + "." + tail
    try: return float(s)
    except ValueError: return None

def close(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= max(tol, abs(b) * 0.001)

def _numeric_dims(dims):
    """치수 목록 [[L,W,H,n], ...] 에서 네 값이 모두 숫자인 것만 돌려준다. postprocess 를 거치지 않은 값이나
    글자가 섞인 값이 와도 verify 의 곱셈이 죽지 않게 한다."""
    out = []
    for d in dims or []:
        if not isinstance(d, (list, tuple)) or len(d) != 4:
            continue
        l, w, h, n = (num(v) for v in d)
        if l is None or w is None or h is None:
            continue
        out.append([l, w, h, n or 1])
    return out

def load_code_lists(path):
    """닫힌 코드 목록. 두 가지를 받는다.

    .json  KEROL 실계정에서 받은 목록(`schema/codes.json`). 이쪽이 정본이다.
    .xlsx  SO 업로드 양식의 Code 시트. 양식이 KEROL 보다 좁은 자리가 있다(`tools/kerol_codes.py --compare`).
    """
    if str(path).lower().endswith(".json"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import kerol_codes
        return kerol_codes.load(path)
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True)["Code"]
    lists, cur = {}, None
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = [c for c in r]
        name, code = (r[1] if len(r) > 1 else None), (r[2] if len(r) > 2 else None)
        if name: cur = str(name).strip()
        if cur and code is not None:
            lists.setdefault(cur, set()).add(str(code).strip())
    return lists

def _flight_split_in_text(v, pages) -> bool:
    """편명(LH687)이 운송장에 항공사 코드와 편 번호로 나뉘어 찍혔나.

    AWB 는 「By First Carrier」 칸에 LH, 「Requested Flight/Date」 칸에 687/21.02 를 따로 찍는다(ORI07793063,
    11/11). 둘 사이에 다른 칸 글자가 끼어 LH687 이 붙어 나오지 않는다. 지어낸 편명을 잡는 검사이므로 느슨하게
    하지 않는다. 같은 쪽 안에 코드가 낱말로, 번호가 다른 숫자에 붙지 않은 채로 둘 다 있어야 한다.
    """
    m = re.fullmatch(r"\s*([A-Z0-9]{2})\s*-?\s*0*(\d{1,4})[A-Z]?\s*", str(v or "").upper())
    if not m:
        return False
    code, num = m.groups()
    if not re.search(r"[A-Z]", code):
        return False
    for p in pages:
        t = (p.text + ("\n" + p.ocr if getattr(p, "ocr", "") else "")).upper()
        if re.search(rf"(?<![A-Z0-9]){code}(?![A-Z0-9])", t) and re.search(rf"(?<![\d.,])0*{num}(?![\d]|[.,]\d)", t):
            return True
    return False


def verify(ex: dict, pages, codes: dict):
    """ex: 모델이 낸 JSON. 반환: [(등급, 항목, 메시지)]  등급 ∈ OK / WARN / FAIL"""
    R = []
    def ok(k, m): R.append(("OK", k, m))
    def warn(k, m): R.append(("WARN", k, m))
    def fail(k, m): R.append(("FAIL", k, m))
    # 그림으로 읽은 쪽은 Vision OCR 글도 원문으로 친다. 종류가 갈린 스캔 쪽은 kind 가 SCAN 이 아니라 image 로 표시된다
    full = "\n".join(p.text + ("\n" + p.ocr if getattr(p, "ocr", "") else "") for p in pages)
    flat = re.sub(r"[\s,\-]", "", full).lower()
    has_scan = any(p.kind == "SCAN" or getattr(p, "image", False) for p in pages)

    # (a) 원문 대조: 문자열 값은 원문에 있어야 한다. 모델이 지어낸 값을 여기서 잡는다.
    #     스캔 쪽이 섞여 있으면 값이 그림에서 왔을 수 있으므로 FAIL 대신 WARN 으로 둔다.
    #     대행사 코드와 편명과 PO 번호도 본다. 잡음 실측에서 모델이 못 찾은 칸에 프롬프트의
    #     보기 값을 적었고, 세 케이스가 서류에 없는 같은 IATA 코드를 냈다.
    CURRENCY_MARKS = {"USD": ("USD", "US$", "US $", "$"), "JPY": ("JPY", "¥", "￥", "YEN"), "KRW": ("KRW", "₩", "WON"),
                      "EUR": ("EUR", "€"), "CNY": ("CNY", "RMB", "¥")}
    for k in ("hawb", "mawb", "invoice_no", "incoterms", "currency", "origin", "destination",
              "flight1", "customer_po", "agent_iata_code", "agent_account_no"):
        v = ex.get(k)
        if not v: continue
        if k == "currency" and str(v) in CURRENCY_MARKS:
            # 후처리가 US$ 를 USD 로 바꿨으므로 기호로도 찾는다
            if any(m.lower() in full.lower() for m in CURRENCY_MARKS[str(v)]): ok(k, f"원문에 있음: {v}"); continue
        if k == "mawb" and re.sub(r"\D", "", str(v)) in re.sub(r"\D", "", full):
            ok(k, f"원문에 있음: {v}"); continue      # 180 ICN 5410 2893 처럼 글자가 끼어 찍힌다
        if k == "flight1" and _flight_split_in_text(v, pages):
            ok(k, f"원문에 있음(항공사 코드와 편 번호가 따로 찍힘): {v}"); continue
        if re.sub(r"[\s,\-]", "", str(v)).lower() in flat: ok(k, f"원문에 있음: {v}")
        elif has_scan: warn(k, f"텍스트에 없음. 스캔 쪽에서 읽었을 수 있다: {v}")
        else: fail(k, f"원문에 없는 값: {v}")

    # (b) 인보이스 줄: 수량×단가 = 금액, 금액 합 = 총액
    lines = ex.get("ci_lines") or []
    s_amt, s_qty = 0.0, 0.0
    for i, ln in enumerate(lines, 1):
        q, u, a = num(ln.get("qty")), num(ln.get("unit_price")), num(ln.get("amount"))
        if q is not None and u is not None and a is not None:
            if close(q * u, a): ok(f"ci_line{i}", f"{q:g}×{u:g}={a:g}")
            else: fail(f"ci_line{i}", f"{q:g}×{u:g}={q*u:g} ≠ {a:g}")
        if a is not None: s_amt += a
        if q is not None: s_qty += q
    tot = num(ex.get("ci_total"))
    tax = num(ex.get("ci_tax")) or 0
    if lines and tot is not None:
        if close(s_amt, tot):
            ok("ci_total", f"줄 합 {s_amt:,.2f} vs 총액 {tot:,.2f}")
        elif tax and close(s_amt + tax, tot):
            # 세액이 따로 찍힌 인보이스. 줄 합은 세전이고 총액은 세후다.
            ok("ci_total", f"줄 합 {s_amt:,.2f} + 세액 {tax:,.2f} = 총액 {tot:,.2f}")
        else:
            fail("ci_total", f"줄 합 {s_amt:,.2f} vs 총액 {tot:,.2f}")

    # (c) PL 수량 합 = CI 수량 합, PL 포장 수 = AWB 피스
    pl = ex.get("pl_lines") or []
    if pl and lines:
        pq = sum(num(x.get("qty")) or 0 for x in pl)
        if close(pq, s_qty):
            ok("pl_vs_ci_qty", f"PL {pq:g} vs CI {s_qty:g}")
        elif s_qty == 1:
            # 인보이스가 한 덩어리로 청구하면 수량이 1 이고, 포장 명세서는 낱개를 센다.
            # 세는 단위가 달라서 숫자가 안 맞는 것이 정상이다. 여기서 FAIL 을 내면
            # 멀쩡한 서류가 매번 걸리고, 사람이 그 항목을 안 보게 된다.
            warn("pl_vs_ci_qty", f"PL {pq:g} vs CI {s_qty:g}. 인보이스가 한 덩어리로 청구했습니다")
        else:
            # 포장명세서는 상자나 팔레트를 세고 인보이스는 낱개를 센다. 해상 398건 중 128건이 걸렸다. 수량은 SO 로 안 가므로 경고다.
            warn("pl_vs_ci_qty", f"PL {pq:g} vs CI {s_qty:g}")
    pk, pcs = num(ex.get("pl_packages")), num(ex.get("awb_pieces"))
    if pk is not None and pcs is not None:
        # 포장명세서는 상자를 세고 항공사는 팔레트를 센다. 다른 것이 보통이라 WARN 이다. SO 행은 AWB 피스를 쓴다.
        (ok if close(pk, pcs) else warn)("pkg_vs_pieces", f"PL 포장 {pk:g} vs AWB 피스 {pcs:g}")

    # (d) 총중량: PL = AWB = CI (있는 것끼리)
    gws = {k: num(ex.get(k)) for k in ("pl_gross", "awb_gross", "ci_gross")}
    gws = {k: v for k, v in gws.items() if v is not None}
    if len(gws) >= 2:
        vals = list(gws.values())
        text = " / ".join(f"{k}={v:g}" for k, v in gws.items())
        if all(close(v, vals[0]) for v in vals):
            ok("gross_weight", text)
        else:
            # 🔴 오류와 확인을 발행 주체로 가른다.
            #    같은 주체가 낸 서류끼리(인보이스와 포장명세서) 어긋나면 잘못 읽었거나 서류가
            #    틀린 것이므로 오류다. 운송장 중량은 포워더가 따로 잰 값이라 송하인 서류와
            #    다른 것이 정상이다(배치 82건 중 39건). 그것을 오류로 띄우면 멀쩡한 건이
            #    매번 빨갛게 뜨고, 사람이 그 표시를 안 믿게 된다.
            ship = [gws[k] for k in ("pl_gross", "ci_gross") if k in gws]
            awb = gws.get("awb_gross")
            ship_ok = len(ship) < 2 or close(ship[0], ship[1])
            if ship_ok and awb is not None and ship:
                d = abs(awb - ship[0])
                near = d <= max(2.0, 0.05 * max(awb, ship[0]))
                warn("gross_weight", text +
                     (" (운송장 재계량 차이)" if near else
                      f" (차이 {d:g} kg. 운송장 중량은 포워더가 따로 잽니다. 포장 무게가 드는지 확인하십시오)"))
            else:
                fail("gross_weight", text)
    nws = {k: num(ex.get(k)) for k in ("pl_net", "ci_net")}
    nws = {k: v for k, v in nws.items() if v is not None}
    if len(nws) == 2:
        a, b = nws.values()
        (ok if close(a, b) else fail)("net_weight", f"PL {a:g} vs CI {b:g}")

    # (e) 용적중량: Σ L×W×H/6000 vs chargeable  (chargeable = max(gross, volumetric) 반올림)
    dims = _numeric_dims(ex.get("dims_cm"))      # [[L,W,H,n], ...] 숫자인 것만
    ch, gw = num(ex.get("awb_chargeable")), gws.get("awb_gross")
    if dims and ch is not None:
        vol = sum(l * w * h * n / 6000 for l, w, h, n in dims)
        expect = max(vol, gw or 0)
        # 항공사는 0.5kg 단위 올림이 흔하다
        if abs(round(expect) - ch) <= 0.5 or abs(expect - ch) <= 0.5: ok("chargeable", f"용적 {vol:.1f} / 실중량 {gw} → {ch:g}")
        else: warn("chargeable", f"용적 {vol:.1f}, 실중량 {gw} 인데 chargeable {ch:g}")
    elif ch is not None and gw is not None and ch < gw:
        fail("chargeable", f"chargeable {ch:g} < gross {gw:g}")

    # (i) AWB 신고가액 = CI 총액
    dv = num(ex.get("awb_declared_value"))
    if dv is not None and tot is not None:
        # 🔴 신고가액은 운송장에 포워더가 적는 값이고 인보이스 총액은 송하인이 적는 값이다.
        #    서로 다른 주체의 서류라 어긋나는 것이 곧 우리 판독 오류는 아니다. 확인 대상으로 둔다.
        if close(dv, tot):
            ok("declared_value", f"AWB {dv:,.2f} vs CI {tot:,.2f}")
        else:
            warn("declared_value", f"AWB {dv:,.2f} vs CI {tot:,.2f}. "
                                   f"운송장 신고가액과 인보이스 총액이 다릅니다. "
                                   f"한 운송장에 인보이스가 여럿 실렸는지 확인하십시오")

    # (f) LCA 표: 발행통화 합 = Total, USD 합 = Grand Total, 환율 곱 대조
    lca = ex.get("lca_lines") or []
    if lca:
        s_iss = sum(num(x.get("issue_amt")) or 0 for x in lca)
        s_usd = sum(num(x.get("usd_amt")) or 0 for x in lca)
        t_iss, t_usd = num(ex.get("lca_total_issue")), num(ex.get("lca_grand_total_usd"))
        if t_iss is not None: (ok if close(s_iss, t_iss) else fail)("lca_total", f"{s_iss:,.2f} vs {t_iss:,.2f}")
        if t_usd is not None: (ok if close(s_usd, t_usd) else fail)("lca_usd_total", f"{s_usd:,.2f} vs {t_usd:,.2f}")
        bad, thou = [], []
        for x in lca:
            ia, fx, ua = num(x.get("issue_amt")) or 0, num(x.get("exrate")) or 1, num(x.get("usd_amt"))
            if close(ia * fx, ua, tol=0.02): continue
            # KRW, JPY 표에서 "54.540" 처럼 점이 천 단위 구분자인 경우
            (thou if close(ia * 1000 * fx, ua, tol=0.02) else bad).append(x.get("code"))
        if not bad and not thou: ok("lca_fx", "환율 곱 모두 일치")
        elif thou and not bad: warn("lca_fx", f"점이 천 단위 구분자로 보임(×1000 이면 맞음): {thou}")
        else: warn("lca_fx", f"환율 곱 불일치: {bad}" + (f", 천 단위 점: {thou}" if thou else ""))

    # (g) 코드표: 닫힌 목록 안에 있어야 한다
    if ex.get("package_unit") is None and ex.get("package_unit_raw"):
        warn("package_unit", f"단위 낱말을 코드로 못 바꿨다: {ex['package_unit_raw']}. 사람이 고른다")
    for k, elem in (("incoterms", "incotermsCode"), ("bl_type", "blType"), ("package_unit", "packageUnit"),
                    ("cargo_type", "cargoType"), ("service", "serviceCode")):
        v = ex.get(k)
        if v is None: continue
        lst = codes.get(elem, set())
        (ok if str(v) in lst else fail)(k, f"{v} ∈ {elem}" if str(v) in lst else f"{v} ∉ {elem} ({len(lst)}개)")

    # (j) 대행사 IATA 코드와 계정 번호가 뒤바뀐 것을 잡는다.
    #     정산팀 로그에서 같은 서류를 세 번 돌렸더니 이 둘이 회차마다 자리를 바꿨다.
    #     IATA 코드는 숫자와 사선뿐이고 계정 번호에는 글자가 섞인다.
    iata, acct = ex.get("agent_iata_code"), ex.get("agent_account_no")
    if iata:
        # CASS 꼴로 17-3 0413/001 2, 01-1-9125 0034, 112-0100 8464 처럼 나라마다 다르게 찍힌다.
        # 공백과 하이픈과 사선을 빼면 숫자 7~12자리다. 글자가 섞이면 계정 번호가 온 것이다.
        digits = re.sub(r"[\s\-/]", "", str(iata))
        if re.fullmatch(r"\d{7,12}", digits):
            ok("agent_iata_code", f"형식 맞음: {iata}")
        elif digits.isdigit():
            warn("agent_iata_code", f"IATA 코드 자릿수가 낯설다({len(digits)}자리): {iata}")
        else:
            fail("agent_iata_code", f"IATA 코드 자리에 숫자가 아닌 값: {iata}")
    if iata and acct and str(iata).strip() == str(acct).strip():
        fail("agent_code_vs_account", f"IATA 코드와 계정 번호가 같다: {iata}")

    # (k) AWB 요금 합. 각 칸의 합이 Total Prepaid / Total Collect 와 맞아야 한다.
    parts = {k: num(ex.get(k)) for k in
             ("awb_weight_charge", "awb_valuation_charge", "awb_tax",
              "awb_other_charges_agent", "awb_other_charges_carrier")}
    got = [v for v in parts.values() if v is not None]
    tot_awb = num(ex.get("awb_total"))
    if len(got) >= 2 and tot_awb is not None:
        (ok if close(sum(got), tot_awb) else fail)(
            "awb_charges", f"칸 합 {sum(got):,.2f} vs 합계 {tot_awb:,.2f}")

    # (l) 순중량은 총중량을 넘을 수 없다.
    for a, b, k in (("pl_net", "pl_gross", "pl_net_le_gross"),
                    ("ci_net", "ci_gross", "ci_net_le_gross")):
        x, y = num(ex.get(a)), num(ex.get(b))
        if x is not None and y is not None:
            (ok if x <= y + 0.011 else fail)(k, f"순 {x:g} vs 총 {y:g}")

    # (m) 포장명세서 줄의 중량 합이 총계와 맞아야 한다.
    for key, tot_key, name in (("net", "pl_net", "pl_line_net"), ("gross", "pl_gross", "pl_line_gross")):
        vals = [num(x.get(key)) for x in (ex.get("pl_lines") or [])]
        vals = [v for v in vals if v]           # 0 은 안 적은 것이다. 합 0 vs 총계 7 로 잡히면 안 된다
        t = num(ex.get(tot_key))
        if len(vals) >= 2 and t is not None:
            # 줄 중량은 KEROL 로 안 간다. 낱개 중량을 줄에 적는 양식이 많아 어긋남이 보통이라 WARN 이다.
            (ok if close(sum(vals), t) else warn)(name, f"줄 합 {sum(vals):g} vs 총계 {t:g}")

    # (n) 용적은 치수에서 나온다. 세제곱센티미터를 세제곱미터로 바꾼다.
    cbm = num(ex.get("measurement_cbm"))
    if dims and cbm is not None:
        calc = sum(l * w * h * n for l, w, h, n in dims) / 1e6
        (ok if close(calc, cbm, tol=0.05) else warn)(
            "measurement_cbm", f"치수에서 {calc:.3f} vs 적힌 값 {cbm:g}")

    # (o) 인코텀즈와 운임 조건. 매도인이 운임을 내는 조건이면 선불이어야 한다.
    #     예외가 있는 거래라 FAIL 이 아니라 WARN 이다.
    inc = str(ex.get("incoterms") or "").strip().upper()[:3]
    term = str(ex.get("freight_term") or "").strip().upper()
    # (q) 운임 조건은 둘 중 하나다. 다른 글자가 오면 칸을 잘못 읽은 것이다.
    if term and term not in ("PREPAID", "COLLECT"):
        fail("freight_term", f"PREPAID 나 COLLECT 가 아닌 값: {term}")
    SELLER_PAYS = {"CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP", "DDU"}
    BUYER_PAYS = {"EXW", "FCA", "FOB", "FAS"}
    if inc and term in ("PREPAID", "COLLECT"):
        want = "PREPAID" if inc in SELLER_PAYS else ("COLLECT" if inc in BUYER_PAYS else None)
        if want is None: pass
        elif want == term: ok("incoterms_vs_freight", f"{inc} 이면 {term} 가 맞다")
        else: warn("incoterms_vs_freight", f"{inc} 인데 {term} 다. {want} 가 보통이다")

    # (p) 하우스 번호와 마스터 번호는 다른 번호다.
    h, mm = ex.get("hawb"), ex.get("mawb")
    if h and mm:
        a2, b2 = (re.sub(r"\D", "", str(x)) for x in (h, mm))
        if a2 and a2 == b2: fail("hawb_vs_mawb", f"하우스와 마스터가 같은 번호다: {h}")
        else: ok("hawb_vs_mawb", "서로 다르다")

    # (h) 번호 형식
    m = ex.get("mawb")
    if m:
        d = re.sub(r"\D", "", str(m))
        if len(d) == 11:
            # IATA 검증 숫자: 앞 7자리 mod 7 = 마지막 자리. 원문에 그대로 찍힌 번호가 안 맞으면 서류 쪽 일이라 WARN 이다
            if int(d[3:10]) % 7 == int(d[10]): ok("mawb_check_digit", f"{m} → {d[:3]}-{d[3:]}")
            elif d in re.sub(r"\D", "", full): warn("mawb_check_digit", f"검증 숫자가 안 맞지만 원문에 그대로 있다: {m}")
            else: fail("mawb_check_digit", f"{m} → {d[:3]}-{d[3:]}")
        elif str(m).strip().replace("-", "").isdigit(): warn("mawb_format", f"MAWB 자릿수 {len(d)} (11 이어야)")
        # 해상 마스터 B/L 은 글자가 섞인 번호라 자릿수 규칙이 없다

    # (q) 하이닉스 판(h1)의 줄 단위 검증. 그 판의 키가 없으면 통째로 건너뛴다.
    # 🔴 왜 따로 세나. 이 판이 내는 값은 GERP 의 품목 표로 간다. 그 표의 한 줄은 PO 번호와
    #    품목 코드가 있어야 PO 에 걸린다. 둘 중 하나가 비면 그 줄은 사람이 손으로 잇는다.
    #    합계만 보는 (b) 로는 이 자리가 안 보인다. 금액 합이 맞아도 PO 가 다 비어 있을 수 있다.
    H_CODE_KEYS = ("sap_code", "iv_no", "hios_no", "material_code", "part_no")
    def h_code(x):
        """이 줄의 품목 코드. 인쇄된 열 이름 순서대로 먼저 찬 것을 쓴다."""
        for k in H_CODE_KEYS:
            v = str((x or {}).get(k) or "").strip()
            if v: return v
        return ""
    h_rows = [x for x in (ex.get("ci_lines") or []) + (ex.get("pl_lines") or []) if isinstance(x, dict)]
    if any("po_no" in x or "sap_code" in x for x in h_rows) or "po_list" in ex or "invoice_totals" in ex:
        for lk, label in (("ci_lines", "인보이스"), ("pl_lines", "포장명세서")):
            rows = [x for x in (ex.get(lk) or []) if isinstance(x, dict)]
            if not rows: continue
            for fk, fname in (("po_no", "PO 번호"), ("__code", "품목 코드")):
                got = sum(1 for x in rows if (h_code(x) if fk == "__code" else str(x.get(fk) or "").strip()))
                nm = f"{lk}_{'code' if fk == '__code' else fk}"
                if got == len(rows): ok(nm, f"{label} {len(rows)}줄 모두 {fname}가 있다")
                elif got == 0:
                    # 서류에 그 열이 없을 수 있다. 값을 지어내는 것보다 비는 편이 낫다.
                    warn(nm, f"{label} {len(rows)}줄 모두 {fname}가 없다. 서류에 그 열이 있는지 봐 주십시오")
                else:
                    warn(nm, f"{label} {len(rows)}줄 중 {got}줄만 {fname}가 있다. 놓친 줄이 있는지 봐 주십시오")

        # 줄의 PO 번호가 원문에 있어야 한다. (a) 와 같은 대조를 줄 단위로 건다.
        seen, bad = [], []
        for x in h_rows:
            v = str(x.get("po_no") or "").strip()
            if not v or v in seen: continue
            seen.append(v)
            if re.sub(r"[\s,\-]", "", v).lower() not in flat: bad.append(v)
        if seen:
            if not bad: ok("line_po_in_text", f"줄 PO {len(seen)}개 모두 원문에 있음")
            elif has_scan: warn("line_po_in_text", f"텍스트에 없음. 스캔 쪽에서 읽었을 수 있다: {', '.join(bad[:5])}")
            else: fail("line_po_in_text", f"원문에 없는 PO: {', '.join(bad[:5])}")

        # 줄의 품목 코드도 원문에 있어야 한다. PO 만 대조하고 코드는 안 봤더니 서류에 없는
        # 코드가 세 줄에 들어온 적이 있다(품목 3줄 우시 표본). 그 칸이 GERP 의 Material 로 간다.
        dropped = [x for lk in ("ci_lines", "pl_lines") for ln in (ex.get(lk) or [])
                   if isinstance(ln, dict) for x in (ln.get("_dropped") or [])]
        unver = [x for lk in ("ci_lines", "pl_lines") for ln in (ex.get(lk) or [])
                 if isinstance(ln, dict) for x in (ln.get("_unverified") or [])]
        if dropped:
            fail("line_code_in_text",
                 f"원문에 없어 지운 코드 {len(dropped)}개: {', '.join(dropped[:4])}")
        elif unver:
            warn("line_code_in_text",
                 f"이미지에서 읽어 원문 대조 불가한 코드 {len(unver)}개. 그림과 함께 확인하십시오")
        else:
            codes = [h_code(x) for x in h_rows if h_code(x)]
            if codes:
                ok("line_code_in_text", f"줄 품목 코드 {len(codes)}개 모두 원문에 있음")

        # 헤더에 나열된 PO 와 줄에서 모은 PO 가 같아야 한다. 인보이스 헤더에 PO 가 여럿
        # 나열되는 서류가 있다. 둘이 어긋나면 줄을 빠뜨렸거나 헤더를 놓친 것이다.
        lst = [str(x).strip() for x in (ex.get("po_list") or []) if str(x).strip()]
        if lst or seen:
            miss = [x for x in lst if x not in seen]
            extra = [x for x in seen if x not in lst]
            if lst and not miss and not extra: ok("po_list_vs_lines", f"헤더와 줄의 PO {len(lst)}개가 같다")
            elif not lst: warn("po_list_vs_lines", f"헤더 PO 목록이 비었는데 줄에는 {len(seen)}개가 있다")
            else: warn("po_list_vs_lines",
                       f"헤더에만 {len(miss)}개, 줄에만 {len(extra)}개 있다"
                       + (f". 헤더에만: {', '.join(miss[:4])}" if miss else "")
                       + (f". 줄에만: {', '.join(extra[:4])}" if extra else ""))

        # 인보이스가 여러 장이면 장마다 합을 따로 맞춘다.
        # 🔴 왜 필요한가. 한 묶음에 인보이스 네 장이 들어오고 줄이 스물 넘는 서류가 있다.
        #    (b) 의 「줄 합 = ci_total」 은 그때 반드시 어긋나고, 장마다 맞는지 안 맞는지는
        #    안 보인다. 수리 인보이스처럼 같은 품목이 두 표에 찍히면 줄이 두 배로 들어오는데
        #    그 사고도 여기서 장 단위로 드러난다.
        tots = [x for x in (ex.get("invoice_totals") or []) if isinstance(x, dict)]
        if tots:
            per = {}
            for x in (ex.get("ci_lines") or []):
                if not isinstance(x, dict): continue
                per[str(x.get("invoice_no") or "")] = (per.get(str(x.get("invoice_no") or "")) or 0) + (num(x.get("amount")) or 0)
            for t in tots:
                inv, tv = str(t.get("invoice_no") or ""), num(t.get("total"))
                if tv is None: continue
                if inv not in per: warn(f"inv_total[{inv or '?'}]", f"총액 {tv:,.2f} 인데 이 번호를 단 줄이 없다")
                elif close(per[inv], tv): ok(f"inv_total[{inv}]", f"줄 합 {per[inv]:,.2f} vs 총액 {tv:,.2f}")
                elif tax and close(per[inv] + tax, tv):
                    ok(f"inv_total[{inv}]", f"줄 합 {per[inv]:,.2f} + 세액 {tax:,.2f} = 총액 {tv:,.2f}")
                else: fail(f"inv_total[{inv}]", f"줄 합 {per[inv]:,.2f} vs 총액 {tv:,.2f}")
            orphan = [k for k in per if k and not any(str(t.get("invoice_no") or "") == k for t in tots)]
            if orphan: warn("inv_total_orphan", f"총액이 없는 인보이스 번호: {', '.join(orphan[:4])}")
            # 인보이스 번호 자리에 PO 번호가 들어오는 일이 있다. 같은 표에 나란히 찍혀 있어서다.
            # 둘이 겹치면 장별 합이 맞아도 장을 잘못 가른 것이라 표의 「인보이스」 시트가 틀린다.
            mixed = [k for k in per if k and k in lst]
            if mixed: warn("invoice_no_looks_like_po", f"인보이스 번호가 PO 번호와 같다: {', '.join(mixed[:4])}")
            gs = sum(num(t.get("total")) or 0 for t in tots)
            ct = num(ex.get("ci_total"))
            if ct is not None:
                (ok if close(gs, ct) else fail)("ci_total_vs_invoices",
                                                f"인보이스 {len(tots)}장 합 {gs:,.2f} vs ci_total {ct:,.2f}")

        # 통관 용도설명서를 읽었는데 코드 열이 비었으면 알린다. 그 표가 Material 의 유일한
        # 출처인 서류가 있다. 조용히 빈 채로 두면 다음 회차에 숫자가 왜 떨어졌는지 모른다.
        cnl = [x for x in (ex.get("cn_lines") or []) if isinstance(x, dict)]
        if cnl:
            got = sum(1 for x in cnl if str(x.get("sap_code") or "").strip())
            if got == len(cnl): ok("cn_sap_code", f"통관 용도설명서 {len(cnl)}행 모두 코드가 있다")
            elif got == 0: warn("cn_sap_code", f"통관 용도설명서 {len(cnl)}행에서 코드를 못 읽었다. 다시 뽑으면 나아진다")
            else: warn("cn_sap_code", f"통관 용도설명서 {len(cnl)}행 중 {got}행만 코드가 있다")

        # 인보이스 줄과 포장명세서 줄을 품목 코드로 이어 수량을 견준다. 코드로 이어지면
        # GERP 매핑이 코드 매핑이 되고, 품명으로 잇는 불투명이 사라진다(02 문서 2장).
        def by_code(lk):
            d = {}
            for x in (ex.get(lk) or []):
                if not isinstance(x, dict): continue
                c = h_code(x)
                if c: d[c] = (d.get(c) or 0) + (num(x.get("qty")) or 0)
            return d
        ci_c, pl_c = by_code("ci_lines"), by_code("pl_lines")
        if ci_c and pl_c:
            diff = [c for c in set(ci_c) | set(pl_c) if not close(ci_c.get(c, 0), pl_c.get(c, 0))]
            if not diff: ok("ci_pl_by_code", f"품목 코드 {len(ci_c)}개의 수량이 인보이스와 포장명세서에서 같다")
            else: warn("ci_pl_by_code",
                       "코드별 수량이 다르다: " + ", ".join(
                           f"{c} CI {ci_c.get(c, 0):g} vs PL {pl_c.get(c, 0):g}" for c in sorted(diff)[:4]))
    return R

# ---------- 0.5 메일에서 온 값 ----------
def load_case(path):
    """`tools/mailpipe.py` 가 만든 case.json. (이름, 서류 목록, 본문에서 뽑은 값)"""
    with open(path, encoding="utf-8") as f: c = json.load(f)
    return c["case_key"], list(c.get("files") or []), dict(c.get("values") or {})

def apply_mail_values(ex, mail_values):
    """서류에 없는 값을 메일 본문에서 채운다. 서류가 이긴다. 다르면 채우지 않고 알린다.

    회의에서 "BL 에 없는 필수 항목이 메일 본문에 있다"고 한 자리다. 서류와 메일이 다르면
    사람이 봐야 하므로 덮어쓰지 않는다.
    """
    filled, clash = [], []
    for k, v in (mail_values or {}).items():
        if k not in SCHEMA["properties"]: continue
        cur = ex.get(k)
        if cur in (None, "", []):
            ex[k] = v
            ex.setdefault("_from_mail", []).append(k)
            filled.append((k, v))
        elif str(cur).strip().upper() != str(v).strip().upper():
            clash.append((k, cur, v))
    return {"채움": filled, "다름": clash}

# ---------- 4.5 코드 찾기 ----------
# 서류에는 이름이 적혀 있고 KEROL 은 코드를 받는다. 이 사이를 잇는 자리다. 모델을 부르지 않는다.
def load_masters(company=None, location=None, learned=None, provisional=False):
    """거래처와 장소 색인. 파일이 없으면 그 자리는 비운다. 없는 자리는 사람에게 넘어간다."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import codefind, kerol_master
    m = {}
    if company:
        m["company"] = codefind.CodeIndex(codefind.load_master(company), learned=learned)
    loc = location or os.path.join(SCHEMA_DIR, "locations.json")
    if os.path.exists(loc):
        m["location"] = codefind.CodeIndex(kerol_master.load_locations(loc, provisional=provisional))
    return m

RESOLVE = (   # 넣을 자리, 이름 키, 주소 키, 어느 마스터
    ("shipper", "shipper_name", "shipper_address", "company"),
    ("consignee", "consignee_name", "consignee_address", "company"),
    ("pol", "origin", None, "location"),
    ("pod", "destination", None, "location"),
)

def resolve_codes(ex, masters):
    """이름에서 코드를 찾아 ex 에 넣는다. 확정만 채우고 나머지는 후보로 남겨 사람에게 넘긴다."""
    report = {}
    for field, name_key, addr_key, which in RESOLVE:
        idx, nm = masters.get(which), ex.get(name_key)
        if not idx or not nm: continue
        d = idx.decide(nm, ex.get(addr_key) if addr_key else None)
        d["이름"] = nm
        report[field] = d
        if d["status"] == "확정": ex[field + "_code"] = d["code"]
    return report

# ---------- 5. 오더 행 ----------
SO_COLS = ["Order Date","Customer Order No","HBL No","MBL No","Act. Customer","Agent","BL Type","Shipper","Consignee",
 "Carrier","Flight / Vessel","Voyage","Trucker","Incoterms","Payment Term Code","Other Term Code","Item Category","Item Desc",
 "Receipt Location","Port of Loading","Port of Discharge","Delivery Location","Receipt Date","ETD","ETD Time","ETA","ETA Time",
 "Delivery Date","Invoice Amt","Invoice Cur","UDF","Cargo Type","Package Qty.","Package Unit","Gross Weight","Gross Weight Unit",
 "Chargeable Weight","Chargeable Weight Unit","Ref. Type","Ref. No.","Remark","TS1","By 1 Code","Flight1 ETD Date","Flight1 ETD Time",
 "Flight1 ETA Date","Flight1 ETA Time","TS2","By 2 Code","2nd Flight No.","Flight2 ETD Date","Flight2 ETD Time","Flight2 ETA Date",
 "Flight2 ETA Time","Order Type","Service Group","Service","Act. Customer PIC ID","Cntr. Qty.","Cntr. Type"]

def to_so_row(ex):
    row = {c: "" for c in SO_COLS}
    g = ex.get
    row["Customer Order No"] = g("customer_po") or ""
    row["HBL No"] = g("hawb") or ""
    row["MBL No"] = re.sub(r"\D", "", str(g("mawb") or ""))
    row["Incoterms"] = g("incoterms") or ""
    row["Item Desc"] = g("item_desc") or ""
    row["Invoice Amt"] = num(g("ci_total")) if g("ci_total") is not None else ""
    row["Invoice Cur"] = g("currency") or ""
    row["Package Qty."] = g("awb_pieces") or g("pl_packages") or ""    # 항공사가 센 피스가 운송 단위다
    row["Package Unit"] = g("package_unit") or ""
    row["Gross Weight"] = g("awb_gross") or g("pl_gross") or ""
    row["Gross Weight Unit"] = "KG" if row["Gross Weight"] != "" else ""
    row["Chargeable Weight"] = g("awb_chargeable") or ""
    row["Chargeable Weight Unit"] = "KG" if row["Chargeable Weight"] != "" else ""
    row["Ref. Type"], row["Ref. No."] = ("INV", g("invoice_no")) if g("invoice_no") else ("", "")
    row["Service Group"] = (g("transport_mode") or "AIR") if g("hawb") or g("mawb") else ""
    row["Flight / Vessel"] = g("flight1") or ""
    # 코드 찾기가 확정한 것만 넣는다. 못 찾은 자리는 비워 두고 확인 화면에서 사람이 채운다
    for col, key in (("Shipper", "shipper_code"), ("Consignee", "consignee_code"),
                     ("Port of Loading", "pol_code"), ("Port of Discharge", "pod_code")):
        if g(key): row[col] = g(key)
    return row

# ---------- 실행 ----------
def run(bundles, codes_xlsx, extractions=None, show=True, llm=None, model=None, anthropic=False, vision=False,
        truth=None, llm_key=None, upstage_ie=False, masters=None, customer=None, mail_values=None,
        claude_cli=False, claude_bin="claude", claude_think=False, claude_config_dir=None, split=None, prepared=None):
    """bundles: [(이름, [pdf 경로, ...]), ...]. llm 은 OpenAI 호환 /v1 주소, anthropic=True 면 Anthropic API.
    prepared: {이름: 쪽들}. 부르는 쪽이 이미 쪽을 가르고 스캔 쪽을 OCR 해 두었으면 그 쪽들을 그대로 쓴다.
    (그림 파일 page.png 를 지우는 것도 부르는 쪽 몫이다.)"""
    codes = load_code_lists(codes_xlsx) if codes_xlsx else {}
    masters = masters or {}
    if split is None: split = SPLIT
    rows = []
    extractions = dict(extractions or {})
    for name, paths in bundles:
        t0 = time.perf_counter()
        # 🔴 화면(wuxi_engine.read_bundle)은 공급사를 알아보려고 이미 쪽을 가르고 스캔 쪽을 OCR 했다. 여기서
        #    다시 하면 같은 OCR 을 두 번 한다. 스캔 4쪽짜리 건에서 OCR 한 번에 20초쯤 들어 건당 시간의 3분의 1이
        #    중복이었다(11/15). 받은 쪽이 있으면 그대로 쓴다. 결과는 같다(같은 파일, 같은 엔진, 같은 해상도).
        if prepared and name in prepared:
            pages, scan_dir = prepared[name], None
        else:
            pages = apply_awb_sort([classify(p) for path in paths for p in load_pdf(path)])
            scan_dir = ocr_pages(pages)
            route_scans(pages)
        t1 = time.perf_counter()
        sel = select_pages(pages)
        kept = set(id(p) for p in sel)
        for p in pages:
            p.dropped = id(p) not in kept
        scans = [p for p in sel if p.needs_image]
        in_tok = sum(est_tokens_text(p.text) for p in sel if p.kind != "SCAN")
        img_tok, img_ms = 0, 0.0
        for p in scans:
            # 토큰 어림에 그림을 다시 그리지 않는다. 쪽 크기만 본다(한 쪽 0.2초를 아낀다)
            try:
                rect = pymupdf.open(p.file)[p.no - 1].rect; w, h = rect.width * 150 / 72, rect.height * 150 / 72
            except Exception:
                w, h = 1240, 1754
            scale = min(1.0, 1568 / max(w, h)); img_tok += est_tokens_image(w * scale, h * scale)
        total_in = in_tok + img_tok
        t2 = time.perf_counter()
        r = dict(file=name, pages=len(pages), kinds=" ".join(p.kind for p in pages), sent=len(sel), scan=len(scans),
                 tok_text=in_tok, tok_img=img_tok, ms_parse=(t1 - t0) * 1000, ms_select=(t2 - t1) * 1000 - img_ms, ms_render=img_ms,
                 usd_haiku=cost(total_in, model="haiku-4.5"), usd_sonnet=cost(total_in, model="sonnet-5"), usd_opus=cost(total_in, model="opus-5"))
        if llm or anthropic or upstage_ie or claude_cli:
            msgs = build_messages(sel, vision=vision)
            try:
                if upstage_ie and split:
                    raw, stat = call_split(model, pages, paths, {}, caller="upstage_ie")
                elif upstage_ie:
                    raw, stat = call_upstage_ie(sel)   # 한 번에 부를 때도 고른 쪽만 보낸다
                elif claude_cli and split:
                    raw, stat = call_split(model, pages, paths, dict(binary=claude_bin, think=claude_think, config_dir=claude_config_dir))
                elif claude_cli:
                    imgs = tempfile.mkdtemp(prefix="docpipe-img-")
                    try:
                        raw, stat = call_claude_cli(model, msgs,
                                                    paths=(scan_images(sel, imgs) if SEND_SCAN_IMAGES else []),
                                                    binary=claude_bin, think=claude_think, config_dir=claude_config_dir)
                    finally:
                        shutil.rmtree(imgs, ignore_errors=True)
                elif anthropic and split:
                    raw, stat = call_split(model, pages, paths, {}, caller="anthropic")
                elif anthropic: raw, stat = call_anthropic(model, msgs)
                elif split:
                    raw, stat = call_split(model, pages, paths, {"base": llm, "key": llm_key, "vision": vision}, caller="openai")
                else: raw, stat = call_openai_compat(llm, model, msgs, key=llm_key)
                extractions[name] = postprocess(raw, customer=customer); r["llm"] = stat; r["raw"] = raw
            except RateLimited:
                raise                  # 다음 꾸러미를 불러도 같은 답이다. 부르는 쪽이 멈춘다
            except Exception as e:      # 서버가 죽거나 JSON 이 깨진 경우. 꾸러미 하나 때문에 전체를 멈추지 않는다
                r["llm"] = dict(error=repr(e)[:300])
        if name in extractions:
            ex = postprocess(extractions[name], customer=customer)   # 파일에서 읽은 모델 원출력도 같은 꼴로 다듬는다
            awb_text = "\n".join(p.text for p in pages if p.kind == "AWB")
            filled = {}
            awb_digits = re.sub(r"\D", "", awb_text)
            is_sea = any(getattr(p, "sea", False) for p in pages)
            if is_sea:
                # 해상 마스터 B/L 은 글자 섞인 번호라 11자리 규칙이 없다. 숫자 찾기를 걸면 수하인 전화번호가
                # 마스터 자리에 들어갔다(해상 30건 중 2건). 하우스 번호도 마스터를 집을 수 있어 안 찾는다.
                before = dict(ex)
                sea_fix(ex, pages)
                ex["transport_mode"] = "SEA"
                for k in ("freight_term", "flight1", "item_desc", "package_unit", "hawb", "mawb", "shipper_name", "consignee_name", "origin", "destination", "transport_mode"):
                    if ex.get(k) != before.get(k) and isinstance(r.get("raw"), dict):
                        r["raw"][k] = ex.get(k); r.setdefault("filled", []).append(k)
            else:
                if not ex.get("mawb") or re.sub(r"\D", "", str(ex["mawb"])) not in awb_digits:
                    # 모델이 낸 마스터가 원문에 없으면(자리를 바꿔 적은 180-20842614) 원문에서 찾은 것으로 바꾼다
                    found = find_mawb(awb_text)
                    if found: filled["mawb"] = found
                if not ex.get("hawb"):
                    filled["hawb"] = find_hawb(awb_text, ex.get("mawb") or filled.get("mawb"))
            for k, v in filled.items():
                if v:
                    ex[k] = v
                    r.setdefault("filled", []).append(k)
            if isinstance(r.get("raw"), dict):
                # 저장되는 원출력에도 번호를 남긴다. 후처리가 하우스 자리의 마스터를 옮긴 결과까지 포함해서.
                for k in ("hawb", "mawb"):
                    r["raw"][k] = ex.get(k)
            if mail_values: r["mail"] = apply_mail_values(ex, mail_values)
            tv = time.perf_counter(); res = verify(ex, pages, codes); r["ms_verify"] = (time.perf_counter() - tv) * 1000
            r["routed"] = " ".join(f"{p.no}:{p.routed}" for p in pages if getattr(p, "routed", ""))
            tc = time.perf_counter(); r["codes"] = resolve_codes(ex, masters); r["ms_codes"] = (time.perf_counter() - tc) * 1000
            r["verify"] = res; r["so_row"] = to_so_row(ex); r["ex"] = ex
            if truth and name in truth: r["compare"] = compare(ex, truth[name])
        rows.append(r)
        if scan_dir: shutil.rmtree(scan_dir, ignore_errors=True)
    return rows

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="서류 꾸러미를 읽고 분류하고 모델 입력 크기와 비용을 센다. 추출 JSON 이 있으면 규칙 검증까지 한다.")
    ap.add_argument("pdf", nargs="*", help="PDF 하나가 꾸러미 하나")
    ap.add_argument("--bundle", action="append", default=[], metavar="이름=a.pdf,b.pdf", help="PDF 여러 개를 꾸러미 하나로 묶는다")
    ap.add_argument("--codes", help="닫힌 코드 목록. schema/codes.json(KEROL 실목록) 또는 SO 업로드 양식 xlsx")
    ap.add_argument("--extractions", help="꾸러미 이름 → 추출 JSON. 모델이 낸 값을 검증한다")
    ap.add_argument("--llm", metavar="URL", help="OpenAI 호환 서버의 /v1 주소. 예: http://127.0.0.1:8080/v1 (llama-server, Ollama, vLLM)")
    ap.add_argument("--anthropic", action="store_true", help="Anthropic API 로 부른다. ANTHROPIC_API_KEY 가 필요하다")
    ap.add_argument("--claude-cli", action="store_true", help="Claude Code CLI 를 서브프로세스로 부른다. API 키 없이 구독으로 돈다. 스키마는 MCP 도구로 강제하고 스캔본은 Read 가 그림으로 읽는다")
    ap.add_argument("--claude-think", action="store_true", help="CLI 의 생각 토큰을 켠다. 기본은 끈다. 서류를 옮겨 적는 일이라 생각이 시간만 먹는다")
    ap.add_argument("--claude-bin", default="claude", help="claude 실행 파일 경로. PATH 에 없으면 `which claude` 가 알려 준 경로를 준다")
    ap.add_argument("--claude-config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR"), help="CLI 설정 폴더. 다른 계정으로 돌릴 때 준다")
    ap.add_argument("--no-split", action="store_true", help="서류별로 나눠 부르지 않고 꾸러미를 한 번에 준다")
    ap.add_argument("--model", default="claude-sonnet-5", help="모델 이름. haiku 는 AWB 한 쪽을 통째로 비워 낸 적이 있고 sonnet 이 더 빨랐다(10초 vs 24초)")
    ap.add_argument("--llm-key", default=os.environ.get("LLM_API_KEY"), help="원격 OpenAI 호환 서버의 키 (Bearer). 환경변수 LLM_API_KEY")
    ap.add_argument("--upstage-ie", action="store_true", help="Upstage Information Extract 로 부른다. 고른 쪽을 서류별로 나눠 보낸다. UPSTAGE_API_KEY 가 필요하다")
    ap.add_argument("--vision", action="store_true", help="스캔 쪽을 그림으로 보낸다")
    ap.add_argument("--save", help="모델이 낸 추출 JSON 을 이 파일에 적는다")
    ap.add_argument("--save-full", help="후처리한 값과 검증 결과를 같이 적는다. tools/gerp_xlsx.py 가 읽는다")
    ap.add_argument("--truth", help="정답 JSON. 모델 출력과 항목별로 견준다")
    ap.add_argument("--prompt", default=PROMPT_VERSION, choices=sorted(PROFILES),
                    help="v3 은 거래처 이름과 주소까지 뽑는다. v4 는 검증을 걸 수 있는 칸을 더 뽑는다. "
                         "04 문서의 실측을 그대로 되풀이하려면 v2 를 준다.")
    ap.add_argument("--company-master", help="거래처 마스터 (CSV code,name,address). KEROL Company 화면에서 받는다")
    ap.add_argument("--location-master", help="장소 마스터 JSON. 기본은 schema/locations.json")
    ap.add_argument("--learned", help="담당자가 확정한 코드 매핑 JSON. 코드 찾기가 먼저 본다")
    ap.add_argument("--provisional-locations", action="store_true", help="확인하지 않은 임시 장소 코드도 쓴다")
    ap.add_argument("--customer", help="고객 코드. 단위 변칙 표에서 이 고객의 줄을 쓴다")
    ap.add_argument("--case", help="mailpipe.py 가 만든 case.json. 서류 목록과 메일 본문의 값을 같이 쓴다")
    a = ap.parse_intermixed_args(argv)   # PDF 와 --bundle 을 섞어 써도 된다
    bundles = [(os.path.basename(p)[:-4] if p.lower().endswith(".pdf") else os.path.basename(p), [p]) for p in a.pdf]
    for b in a.bundle:
        name, files = b.split("=", 1); bundles.append((name, files.split(",")))
    mail_values = None
    if a.case:
        cname, cfiles, mail_values = load_case(a.case)
        if cfiles: bundles.append((cname, cfiles))
    if not bundles: ap.error("PDF 나 --case 를 하나 이상 준다")
    ex = json.load(open(a.extractions, encoding="utf-8")) if a.extractions else None
    truth = json.load(open(a.truth, encoding="utf-8")) if a.truth else None
    t0 = time.perf_counter()
    set_prompt(a.prompt)
    masters = load_masters(a.company_master, a.location_master, a.learned, a.provisional_locations)
    rows = run(bundles, a.codes, extractions=ex, llm=a.llm, model=a.model, anthropic=a.anthropic, vision=a.vision,
               claude_cli=a.claude_cli, claude_bin=a.claude_bin, claude_think=a.claude_think, claude_config_dir=a.claude_config_dir, split=not a.no_split,
               truth=truth, llm_key=a.llm_key, upstage_ie=a.upstage_ie, masters=masters, customer=a.customer,
               mail_values=mail_values)
    wall = (time.perf_counter() - t0) * 1000
    failed_all = False
    if a.save:
        json.dump({r["file"]: r["raw"] for r in rows if "raw" in r}, open(a.save, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.save_full:
        # --save 는 모델이 낸 날것만 적는다. 표를 만드는 쪽은 검증 결과도 있어야 「검토」 칸을
        # 채운다. 날것만 받아서 표를 그리면 FAIL 이 하나도 없는 표가 나오고, 조용히 통과한다.
        json.dump({r["file"]: {"ex": r.get("ex"), "verify": r.get("verify") or [],
                               "so_row": r.get("so_row")} for r in rows if "raw" in r},
                  open(a.save_full, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{'꾸러미':14} {'쪽':>3} {'보냄':>3} {'스캔':>3} {'텍스트tok':>9} {'이미지tok':>9} {'파싱ms':>7} {'렌더ms':>7} {'Haiku$':>8} {'Sonnet$':>8} {'Opus$':>8}  쪽 분류")
    tot = dict(tok=0, h=0.0, s=0.0, o=0.0)
    for r in rows:
        print(f"{r['file']:14} {r['pages']:3d} {r['sent']:3d} {r['scan']:3d} {r['tok_text']:9d} {r['tok_img']:9d} {r['ms_parse']:7.1f} {r['ms_render']:7.1f} {r['usd_haiku']:8.4f} {r['usd_sonnet']:8.4f} {r['usd_opus']:8.4f}  {r['kinds']}")
        tot['tok'] += r['tok_text'] + r['tok_img']; tot['h'] += r['usd_haiku']; tot['s'] += r['usd_sonnet']; tot['o'] += r['usd_opus']
    print(f"{'합계':14} {'':3} {'':3} {'':3} {tot['tok']:19d} {'':15} {tot['h']:8.4f} {tot['s']:8.4f} {tot['o']:8.4f}")
    # claude_cli 를 여기서 빠뜨려서 CLI 로 부른 호출이 죽어도 아래 실패 줄이 안 찍혔다.
    # 화면에는 파싱 표만 나오고 왜 값이 없는지는 안 나온다. 조용한 실패가 제일 나쁘다.
    called = a.llm or a.anthropic or a.upstage_ie or a.claude_cli
    print(f"\n벽시계 ({'모델 호출 포함' if called else '모델 호출 제외'}, 검증 포함): {wall:.0f} ms / {len(rows)} 꾸러미")
    if called:
        print(f"\n모델 {a.model}")
        # 앞 판은 이 표에 wall 을 그대로 찍으면서 머리글에 "초"라고 적었다. wall 은 ms 다.
        # 그리고 입력tok 에 input_tokens 만 찍었는데, CLI 로 부르면 그 값이 18 처럼 나온다.
        # 실제 입력은 대부분 캐시 두 칸에 들어가 있어서 표만 보면 공짜로 보였다.
        print(f"{'꾸러미':14} {'초':>6} {'호출':>4} {'좌표':>4} {'입력tok':>8} {'캐시읽기':>8} {'출력tok':>7} {'실비$':>9}")
        tot_cost, tot_in, tot_out, partial, n_cost = 0.0, 0, 0, False, 0
        for r in rows:
            s = r.get("llm", {})
            if "error" in s: print(f"{r['file']:14} 실패: {s['error']}"); continue
            n_in = (s.get("prompt_tokens") or 0) + (s.get("cache_write") or 0)
            n_read = s.get("cache_read") or 0
            n_out = s.get("completion_tokens") or 0
            # CLI 쪽이 비어 있으면 API 쪽을 본다. 안 부른 경로는 0 으로 들어오므로 0 도 빈 값이다.
            usd = s.get("cli_cost_usd")
            if usd is None: usd = s.get("usd") or None
            tot_in += n_in + n_read; tot_out += n_out
            if usd is None:
                partial = True
            else:
                tot_cost += usd; n_cost += 1
            print(f"{r['file']:14} {s['wall'] / 1000:6.1f} {s.get('calls') or 1:4d} {s.get('form_hits') or 0:4d} "
                  f"{n_in:8d} {n_read:8d} {n_out:7d} "
                  + (f"{'-':>9} *" if usd is None else f"{usd:9.4f}"))
        n_ok = sum(1 for r in rows if "error" not in (r.get("llm") or {}))
        if n_ok:
            print(f"{'합계':14} {'':6} {'':4} {'':4} {tot_in:8d} {'':8} {tot_out:7d} "
                  + (f"{tot_cost:9.4f}" if n_cost else f"{'-':>9}"))
            avg = f"실비 ${tot_cost / n_cost:.4f}, " if n_cost else ""
            print(f"꾸러미당 평균: {avg}입력 {tot_in // n_ok:,} 토큰, 출력 {tot_out // n_ok:,} 토큰")
        if partial:
            # 여기에 우리가 곱한 값을 찍어 봤는데 같은 케이스에서 $0.5072 로, 끝까지 기다려
            # 받은 $0.2259 의 2.2배였다. 캐시 읽기로 갈 몫이 입력 자리에 잡히기 때문이다.
            print("* 표시한 줄은 CLI 의 결과 사건을 안 기다리고 끊은 호출이라 실비를 못 냅니다. "
                  "토큰도 출력은 받아 낸 JSON 으로 채운 값입니다. 비용을 재려면 "
                  "DOCPIPE_WAIT_RESULT=1 로 돌리십시오. 케이스당 1초쯤 더 걸립니다.")
        if all("error" in (r.get("llm") or {}) for r in rows):
            print("\n호출이 전부 실패했습니다. 위의 실패 줄이 이유입니다.")
            failed_all = True
    for r in rows:
        if "verify" not in r: continue
        res = r["verify"]; n = {k: sum(1 for g, _, _ in res if g == k) for k in ("OK", "WARN", "FAIL")}
        print(f"\n### {r['file']}  검증 {len(res)}건: OK {n['OK']} / WARN {n['WARN']} / FAIL {n['FAIL']}  ({r['ms_verify']:.2f} ms)")
        for g, k, m in res: print(f"  [{g:4}] {k:18} {m}")
        if r.get("mail"):
            m = r["mail"]
            if m["채움"]: print("  메일 본문에서 채운 값: " + ", ".join(f"{k}={v}" for k, v in m["채움"]))
            for k, doc_v, mail_v in m["다름"]:
                print(f"  [다름] {k:18} 서류 {doc_v} / 메일 {mail_v}. 서류 값을 두고 사람이 봅니다")
        if r.get("codes"):
            print(f"  코드 찾기 ({r['ms_codes']:.2f} ms)")
            for field, d in r["codes"].items():
                cand = ", ".join(f"{c['code']}({c['score']:.2f})" for c in d["candidates"][:3]) or "없음"
                print(f"    [{d['status']:2}] {field:10} {str(d['이름'])[:30]:30} → {cand}")
        filled = {k: v for k, v in r["so_row"].items() if v != ""}
        print(f"  SO 행: {len(SO_COLS)}열 중 {len(filled)}열 채움 → " + json.dumps(filled, ensure_ascii=False))
        if "compare" in r:
            c = r["compare"]; hit = sum(1 for _, ok, _ in c if ok)
            print(f"  정답 대조 {hit}/{len(c)} 항목 일치")
            for k, ok, m in c:
                if not ok: print(f"    [다름] {k:18} {m}")
    return 1 if failed_all else 0

if __name__ == "__main__":
    sys.exit(main())
