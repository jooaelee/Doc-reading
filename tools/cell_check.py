"""판독한 품목 줄의 칸마다 원본 PDF 글자층과 맞춰 본다. 사람이 대조할 칸을 줄이는 데 쓴다(대조 모드).

칸마다 상태 하나
  ok     원본 글자에서 같은 값을 찾음
  miss   원본 글자는 있는데 같은 값을 못 찾음. 사람이 봐야 하는 칸
  ?      견줄 수 없음(스캔이라 글자층이 없거나, 값이 너무 짧거나, 그 줄의 자리를 못 찾음)
  -      빈 칸. 원본의 같은 줄에 채울 만한 값이 보이면 hint 로 알린다

🔴 왜 AI 를 다시 부르지 않나. 판독 값이 원본 글자에 그대로 찍혀 있는지는 글자층만으로 확인된다. 다시 부르면
   사용량을 쓰고, 같은 AI 가 같은 실수를 되풀이할 수 있다. 여기서 「ok」 는 「원본에 같은 글자가 있다」 는
   뜻이지 「그 칸에 들어갈 값이 맞다」 는 뜻은 아니다(예: Sap Code 열 값을 Material 로 고른 것이 맞는지는
   gerp_xlsx.hynix_material 이 정한다). 그래서 확정은 여전히 사람이 한다. 사람은 miss, ?, hint 칸만 본다.
🔴 숫자는 그 줄 안에서만 찾는다. 수량 2 같은 작은 숫자는 쪽 어디에나 있어서 쪽 전체로 찾으면 늘 ok 가 된다.
   그 줄의 코드나 품명이 찍힌 자리를 먼저 찾고(같은 값이 여러 줄이면 차례대로), 그 높이의 글자만 본다.
"""
from __future__ import annotations

import re

import gerp_xlsx

CODE_COLS = ("Purchase Order", "Material", "Vendor Part No.")
NUM_COLS = ("B/L quantity", "Net Price")
CHECK_COLS = CODE_COLS + ("품명",) + NUM_COLS + ("단위", "Currency")
ANCHOR_COLS = ("Material", "Vendor Part No.", "품명", "Purchase Order")
_DASH = re.compile(r"[\s‐-―−\-_]+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PO = re.compile(r"^4[35]\d{8}$")


def _norm_code(v) -> str:
    return _DASH.sub("", str(v if v is not None else "")).upper()


def _norm_text(v) -> str:
    return re.sub(r"[^0-9A-Z가-힣一-鿿]+", "", str(v if v is not None else "").upper())


def _nums(s: str) -> list[float]:
    out = []
    for m in _NUM.findall(s or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _same_num(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.005, abs(b) * 1e-6)


VERSION = 3      # 바꾸면 이미 읽은 작업도 다시 대조한다(wuxi_web). 2: 칸마다 원본 자리(_at). 3: 건 단위 자동 통과(triage)

class Page:
    def __init__(self, page, kind: str, scan: bool, di: int = 0, pn: int = 1):
        self.page = page
        self.kind = kind
        self.di, self.pn = di, pn       # 몇 번째 파일의 몇 쪽인지(화면이 원본 위에 상자를 그릴 때 씀)
        # 엔진의 scan 표시는 로고 그림만 있어도 켜진다. 글자층이 있는지는 글자를 직접 보고 정한다
        raw = page.get_text()
        self.has_text = len(raw.strip()) >= 50
        self.up = raw.upper()
        self.code = _norm_code(raw)
        self.text = _norm_text(raw)
        self.words = page.get_text("words") if self.has_text else []
        self.wordset = {re.sub(r"[^0-9A-Z]", "", w[4].upper()) for w in self.words}

    def band(self, rect, lines: float) -> list[str]:
        """rect 와 같은 높이(위아래로 lines 줄)의 낱말."""
        h = max(4.0, rect.y1 - rect.y0)
        lo, hi = rect.y0 - h * lines, rect.y1 + h * lines
        return [w[4] for w in self.band_words(rect, lines)]

    def band_words(self, rect, lines: float) -> list:
        """band 와 같되 낱말의 자리까지 낸다. 줄 가운데에서 가까운 차례로."""
        h = max(4.0, rect.y1 - rect.y0)
        lo, hi, mid = rect.y0 - h * lines, rect.y1 + h * lines, (rect.y0 + rect.y1) / 2
        ws = [w for w in self.words if lo <= (w[1] + w[3]) / 2 <= hi]
        return sorted(ws, key=lambda w: abs((w[1] + w[3]) / 2 - mid))

    def box(self, r) -> list:
        return [self.di, self.pn, round(r[0], 1), round(r[1], 1), round(r[2], 1), round(r[3], 1)]


def load_pages(docs, meta) -> list[Page]:
    """docs: [(파일 이름, pymupdf 문서)], meta: 엔진의 쪽 정보(payload pages: file, no, kind, scan)."""
    kind = {(m.get("file"), m.get("no")): (m.get("kind") or "", bool(m.get("scan"))) for m in meta or []}
    out = []
    for di, (name, doc) in enumerate(docs):
        if doc is None:
            continue
        for i, pg in enumerate(doc, 1):
            k, scan = kind.get((name, i), ("", False))
            out.append(Page(pg, k, scan, di, i))
    return out


# 원본 위에 상자로 보여 줄 칸. 화면은 줄을 고르면 이 칸들의 자리를 모두 그리고, 고른 칸의 자리를 진하게 그린다
LOC_COLS = ("Purchase Order", "Material", "Vendor Part No.", "품명", "B/L quantity", "단위", "Net Price", "Invoice amount")


def _search(p, text, clip=None) -> list:
    try:
        return list(p.page.search_for(text, clip=clip) if clip is not None else p.page.search_for(text))
    except Exception:
        return []


def locate(row: dict, at, mine: list) -> dict:
    """칸마다 원본 자리 {열: [파일, 쪽, x0, y0, x1, y1]}.

    🔴 그 줄이 찍힌 자리(at)의 높이에서만 찾는다. 예전에는 값을 서류 묶음 첫 쪽부터 찾아, PO 처럼 운송장에도 찍힌 값은
       운송장을 가리켰고 줄마다 같은 PO 면 모든 줄이 한 자리를 가리켰다. 금액은 원본에 「8,608,000.00」 처럼 찍혀
       「8608000」 으로는 못 찾아 상자가 하나도 안 나왔다(COKR26015156, 11/13). 숫자는 낱말을 숫자로 바꿔 견준다.
    🔴 그 줄 자리를 못 찾으면 코드만 같은 종류 서류(인보이스나 포장명세서) 안에서 찾는다. 엉뚱한 자리를 가리키느니 비운다.
    """
    out = {}
    ap, ar = at if at else (None, None)
    for c in LOC_COLS:
        v = str(row.get(c) or "").strip()
        if not v:
            continue
        if c in NUM_COLS + ("Invoice amount",):
            if not ap:
                continue
            try:
                x = float(v.replace(",", ""))
            except ValueError:
                continue
            w = next((w for w in ap.band_words(ar, 3.0) if any(_same_num(y, x) for y in _nums(w[4]))), None)
            if w:
                out[c] = ap.box(w[:4])
        elif c == "단위":
            if not ap:
                continue
            u = re.sub(r"[^A-Z]", "", v.upper())
            w = next((w for w in ap.band_words(ar, 3.0) if u and re.sub(r"[^A-Z]", "", w[4].upper()) == u), None)
            if w:
                out[c] = ap.box(w[:4])
        else:
            q = " ".join(v.split()[:2]) if c == "품명" else v
            if len(_norm_code(q)) < 4:
                continue
            hit = None
            if ap:
                h = max(4.0, ar.y1 - ar.y0)
                clip = (-1e4, ar.y0 - h * 3.0, 1e5, ar.y1 + h * 3.0)
                rs = _search(ap, q, clip)
                if rs:
                    mid = (ar.y0 + ar.y1) / 2
                    hit = (ap, min(rs, key=lambda r: abs((r.y0 + r.y1) / 2 - mid)))
            if not hit and c in CODE_COLS:
                hit = next(((p, r[0]) for p in mine for r in [_search(p, q)] if r), None)
            if hit:
                out[c] = hit[0].box(tuple(hit[1]))
    return out


def _row_nums(row) -> list[float]:
    out = []
    for c in ("B/L quantity", "Net Price", "Invoice amount"):
        try:
            out.append(float(str(row.get(c)).replace(",", "")))
        except (TypeError, ValueError):
            pass
    return out


def _anchor(pages, row, seen):
    """그 줄이 원본 어디에 찍혀 있나. (쪽, 자리) 또는 None.

    같은 코드가 한 쪽에 두 번 찍히거나(품명 안에 코드가 또 들어감) 같은 값이 여러 줄이면 아무 자리나 잡을 수 없다.
    자리마다 그 높이에 이 줄의 수량, 단가, 금액이 몇 개 찍혀 있는지 세어 가장 많은 자리를 고른다. 같으면 차례대로.
    """
    want = _row_nums(row)
    for col in ANCHOR_COLS:
        v = str(row.get(col) or "").strip()
        if col == "품명":
            v = " ".join(v.split()[:2])
        if len(_norm_code(v)) < 4:
            continue
        hits = []
        for p in pages:
            try:
                hits += [(p, r) for r in p.page.search_for(v)]
            except Exception:
                pass
        if not hits:
            continue
        k = seen.get((col, v), 0)
        seen[(col, v)] = k + 1

        def score(h):
            got = [y for w in h[0].band(h[1], 3.0) for y in _nums(w)]
            return sum(1 for x in want if any(_same_num(y, x) for y in got))
        best = max(score(h) for h in hits)
        top = [h for h in hits if score(h) == best]
        return top[min(k, len(top) - 1)] if best else hits[min(k, len(hits) - 1)]
    return None


def check_row(row: dict, pages: list[Page], seen: dict, loc: dict | None = None) -> tuple[dict, dict]:
    """(칸별 상태, 칸별 힌트). loc 을 주면 칸마다 원본 자리를 채운다(locate)."""
    src = row.get("출처") or "CI"
    same = [p for p in pages if p.kind == src]
    # 그 서류(인보이스, 포장명세서)가 스캔이면 견줄 글자가 없다. 다른 서류의 글자로 대신 찾으면 「못 찾음」이 쏟아진다
    mine = [p for p in same if p.has_text] if same else [p for p in pages if p.has_text]
    text_pages = [p for p in pages if p.has_text]
    st, hint = {}, {}
    if not mine:
        return {c: ("-" if not str(row.get(c) or "").strip() else "?") for c in CHECK_COLS}, hint
    at = _anchor(mine, row, seen)
    if loc is not None:
        loc.update(locate(row, at, mine))
    near = at[0].band(at[1], 0.8) if at else []
    wide = at[0].band(at[1], 3.0) if at else []
    for c in CHECK_COLS:
        v = str(row.get(c) or "").strip()
        if not v:
            st[c] = "-"
            continue
        if c in CODE_COLS:
            n = _norm_code(v)
            st[c] = "?" if len(n) < 4 else ("ok" if any(n in p.code for p in text_pages) else "miss")
        elif c == "품명":
            # AI 가 여러 열의 글자를 이어 품명을 만들기도 해서(HDLCA112780) 통째가 아니라 낱말로 본다
            toks = [t for t in (_norm_text(x) for x in re.split(r"[\s/(),:;]+", v)) if len(t) >= 3]
            if not toks:
                st[c] = "?"
            else:
                hay = "".join(p.text for p in mine)
                st[c] = "ok" if sum(1 for t in toks if t in hay) >= max(1, round(len(toks) * 0.7)) else "miss"
        elif c in NUM_COLS:
            try:
                x = float(str(v).replace(",", ""))
            except ValueError:
                st[c] = "miss"
                continue
            if not at:
                st[c] = "?"
            elif any(_same_num(y, x) for w in near for y in _nums(w)) or any(_same_num(y, x) for w in wide for y in _nums(w)):
                st[c] = "ok"
            else:
                st[c] = "miss"
        elif c == "단위":
            # 단위는 줄마다 찍히기도 하고 열 머리에 한 번만 찍히기도 한다(Q'ty(PCS)). 그 줄에 없으면 그 쪽 낱말에서 찾는다
            u = re.sub(r"[^A-Z]", "", v.upper())
            if not u:
                st[c] = "?"
            elif any(u == re.sub(r"[^A-Z]", "", w.upper()) for w in wide) or any(u in p.wordset for p in mine) \
                    or any(re.search(rf"(?<![A-Z]){u}(?![A-Z])", p.up) for p in mine):
                st[c] = "ok"
            else:
                st[c] = "miss"
        elif c == "Currency":
            # 통화는 인보이스 단위 값이다. 포장명세서 줄이라도 인보이스 쪽에서 찾는다
            inv = [p for p in pages if p.has_text and p.kind == "CI"] or text_pages
            cur = v.upper()
            pat = rf"(?<![A-Z]){re.escape(cur)}(?![A-Z])" + (r"|US\s*\$|\$|DOLLAR|美元" if cur == "USD" else "")
            st[c] = "ok" if any(re.search(pat, p.up) for p in inv) else ("?" if not inv else "miss")
    # 빈 칸 힌트: 원본의 같은 줄에 하이닉스 자재 번호나 PO 로 보이는 값이 있으면 알린다
    if st.get("Material") == "-" and wide:
        codes = [w for w in wide if gerp_xlsx.is_hynix_code(w) or re.fullmatch(r"[A-Z]\d{7}", w.upper() or "")]
        if codes:
            hint["Material"] = codes[0]
    if st.get("Purchase Order") == "-":
        pos = sorted({w for w in (wide or []) if _PO.match(w)}) or sorted(
            {w for p in mine for w in (x[4] for x in p.words) if _PO.match(w)})
        if len(pos) == 1:
            hint["Purchase Order"] = pos[0]
    return st, hint


# 비어 있으면 사람이 볼 칸(web/index.html 의 BLANK_FLAG 와 같게). 정답 93건에서 고친 칸이 빈 Material 과 빈 PO 에 몰렸다
BLANK_FLAG = ("Material", "Purchase Order")


def triage(items: list, verdict) -> dict:
    """건 단위로 사람이 볼 건인지 정한다. {"auto": 자동 통과인가, "why": [사람이 볼 까닭], "flags": 확인할 칸 수}.

    자동 통과는 넷을 모두 채울 때만이다. 빠진 줄 판정 「이상 없음」, 확인할 칸(원본에서 못 찾은 칸, 원본 같은 줄에 값이
    있는 빈 칸, 빈 Material 과 PO) 0개, 스캔이라 대조 못 한 칸 없음, 인보이스 줄 있음.
    🔴 확정한 정답 97건(11/14)에 대 보면 이 기준의 자동 통과 21건은 사람이 고친 칸이 하나도 없었다. 스캔 조건을 빼면
       HDLCA125295 를, 빠진 줄 판정까지 빼면 PNS26081814 를 놓친다. 그래서 느슨하게 만들지 않는다.
    화면처럼 인보이스 줄이 있으면 포장명세서에만 있는 줄은 보지 않는다(화면이 표에서 뺀다).
    """
    rows = [r for r in items or [] if isinstance(r, dict)]
    has_ci = any(r.get("출처") != "PL" for r in rows)
    if has_ci:
        rows = [r for r in rows if r.get("출처") != "PL"]
    flags = unk = 0
    for r in rows:
        ok, hint = r.get("_ok") or {}, r.get("_hint") or {}
        for c in CHECK_COLS:
            s, v = ok.get(c), str(r.get(c) if r.get(c) is not None else "").strip()
            if s == "miss" or (s == "-" and hint.get(c)) or (not v and c in BLANK_FLAG):
                flags += 1
            elif s == "?":
                unk += 1
    why = []
    if verdict != "이상 없음":
        why.append("빠진 줄 의심" if verdict == "빠진 줄 의심" else "빠진 줄 확인 못 함")
    if not rows:
        why.append("품목 줄 없음")
    elif not has_ci:
        why.append("인보이스 줄을 못 읽음")
    if flags:
        why.append(f"확인할 칸 {flags}개")
    if unk:
        why.append("스캔 서류")
    return {"auto": not why, "why": why, "flags": flags}


def check_items(items: list, docs, meta) -> dict:
    """품목 줄마다 _ok(칸별 상태), _hint(빈 칸에 넣을 만한 원본 값), _at(칸별 원본 자리)를 붙인다. 모은 수를 낸다.
    _hl(줄을 고르면 원본에 그릴 상자)도 _at 으로 바꾼다. 엔진이 붙인 _hl 은 값을 묶음 첫 쪽부터 찾아 엉뚱한 자리가 많았다."""
    pages = load_pages(docs, meta)
    seen: dict = {}
    total = {"ok": 0, "miss": 0, "?": 0, "-": 0, "hint": 0}
    for row in items or []:
        if not isinstance(row, dict):
            continue
        loc: dict = {}
        st, hint = check_row(row, pages, seen, loc)
        row["_ok"], row["_hint"], row["_at"] = st, hint, loc
        row["_hl"] = list({tuple(b): b for b in loc.values()}.values())
        for s in st.values():
            total[s] = total.get(s, 0) + 1
        total["hint"] += len(hint)
    return total
