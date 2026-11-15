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

import json
import os
import re
import shutil
import tempfile

import gerp_xlsx

CODE_COLS = ("Purchase Order", "Material", "Vendor Part No.")
NUM_COLS = ("B/L quantity", "Net Price")
CHECK_COLS = CODE_COLS + ("품명",) + NUM_COLS + ("단위", "Currency")
ANCHOR_COLS = ("Material", "Vendor Part No.", "품명", "Purchase Order")
_DASH = re.compile(r"[\s‐-―−\-_]+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PO = re.compile(r"^4[35]\d{8}$")


def _val(row: dict, c: str) -> str:
    """칸 값. 0 은 빈 칸이 아니다(무상 품목의 단가 0 도 원본과 견준다)."""
    v = row.get(c)
    return "" if v is None else str(v).strip()


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


VERSION = 4      # 바꾸면 이미 읽은 작업도 다시 대조한다(wuxi_web). 2: 칸마다 원본 자리(_at). 3: 건 단위 자동 통과(triage)
                 # 4: 글자층이 없는 쪽은 OCR 글자로 대조

# 🔴 왜 스캔 쪽을 OCR 하나. 10월 50건 중 29건이 스캔 서류였고, 글자층이 없어 칸을 하나도 견주지 못해 모두 사람에게
#    넘어갔다(자동 통과 49건 중 4건, 11/14). 엔진도 스캔 쪽을 OCR 하지만 쪽 종류를 가르는 데만 쓰고 글자 자리는
#    남기지 않는다. 여기서는 품목 줄이 있는 쪽(그 줄의 출처 서류)만 OCR 하고, 결과를 PDF 옆(<파일>.ocr.json)에
#    남겨 다시 대조할 때 쓴다. 한 쪽에 보통 10초, 줄이 빽빽한 쪽은 1분쯤 든다. OCR 엔진이 없으면 예전처럼 「?」 로 둔다.
OCR_DPI = 150
OCR_V = 1
_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            # 스레드를 기본(코어 수)대로 두면 서로 다투어 더 느리다. 품목 574줄 쪽이 기본 121초, 4개 56초, 16개 177초(11/15)
            _ENGINE = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1)
        except Exception:
            _ENGINE = False
    return _ENGINE or None


def ocr_page(page) -> list:
    """쪽을 그림으로 그려 OCR 한다. 줄마다 [x0, y0, x1, y1, 글] (PDF 좌표, 글자층의 search_for 와 같은 좌표)."""
    eng = _engine()
    if not eng:
        return []
    import numpy as np
    import pymupdf
    pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)
    im = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 1:
        im = np.repeat(im, 3, axis=2)
    res, _ = eng(np.ascontiguousarray(im[:, :, :3]), use_cls=False)
    k, m = 72 / OCR_DPI, page.derotation_matrix
    out = []
    for box, text, _score in res or []:
        xs, ys = [q[0] for q in box], [q[1] for q in box]
        r = pymupdf.Rect(min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k) * m
        out.append([round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1), str(text)])
    return out


def cached_ocr(path):
    """그 PDF 의 쪽 OCR 을 한 번만 하게 PDF 옆 <파일>.ocr.json 에 남긴다. (쪽, 쪽 번호) -> 줄들."""
    f = (path + ".ocr.json") if path and os.path.isfile(path) else None
    cache = {}
    if f and os.path.isfile(f):
        try:
            with open(f, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
    if cache.get("v") != [OCR_V, OCR_DPI]:
        cache = {"v": [OCR_V, OCR_DPI], "pages": {}}

    def read(page, pn: int) -> list:
        key = str(pn)
        if key not in cache["pages"]:
            lines = ocr_page(page)
            if not _engine():
                return lines            # 엔진이 없으면 남기지 않는다(나중에 깔면 그때 읽게)
            cache["pages"][key] = lines
            if f:
                try:
                    tmp = f + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(cache, fh, ensure_ascii=False)
                    os.replace(tmp, f)
                except OSError:
                    pass
        return cache["pages"][key]

    def put(got: dict) -> None:
        """{쪽 번호: 줄들} 을 한꺼번에 남긴다(_prefetch)."""
        cache["pages"].update({str(k): v for k, v in got.items()})
        if f:
            try:
                tmp = f + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh, ensure_ascii=False)
                os.replace(tmp, f)
            except OSError:
                pass
    read.has = lambda pn: str(pn) in cache["pages"]
    read.put = put
    return read


def _pool_ocr(pgs) -> list | None:
    """엔진(docpipe)의 OCR 일꾼들로 여러 쪽을 동시에 읽는다. 쪽마다 ocr_page 와 같은 꼴의 줄들. 못 하면 None."""
    try:
        import docpipe
        import pymupdf
        if docpipe.ocr_engine() != "rapid":
            return None
        pool = docpipe._rapid_pool()
    except Exception:
        return None
    d = tempfile.mkdtemp(prefix="cellocr-")
    try:
        files, geo = [], []
        for i, pg in enumerate(pgs):
            pix = pg.get_pixmap(dpi=OCR_DPI, alpha=False)
            fn = os.path.join(d, "p%03d.png" % i)
            pix.save(fn)
            files.append(fn)
            geo.append((pix.w, pix.h, pg.derotation_matrix))
        res = list(pool.map(docpipe._rapid_one, [(fn, 1.0) for fn in files]))
    except Exception:
        return None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    k, out = 72 / OCR_DPI, []
    for (w, h, m), lines in zip(geo, res):
        one = []
        for t, x, y, bw, bh in lines or []:
            r = pymupdf.Rect(x * w * k, y * h * k, (x + bw) * w * k, (y + bh) * h * k) * m
            one.append([round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1), str(t)])
        out.append(one)
    return out


def _prefetch(items, pages) -> None:
    """품목 줄이 견줄 스캔 쪽(check_row 가 read_ocr 할 쪽)을 미리 한꺼번에 OCR 해 둔다.

    🔴 check_row 는 쪽을 하나씩 OCR 한다(스레드 4개). 스캔 6쪽짜리 건에서 대조에만 3분이 들었다(11/15, 배터리로 도는
       노트북). 판독이 끝나 엔진의 OCR 일꾼들이 놀고 있으므로 그 일꾼들에 쪽을 나눠 동시에 읽힌다."""
    want: list = []
    for src in {(r.get("출처") or "CI") for r in items or [] if isinstance(r, dict)}:
        same = [p for p in pages if p.kind == src]
        for p in same or pages:
            if p not in want:
                want.append(p)
    todo = [p for p in want if not p.has_text and p._ocr is not None and not p._ocr.has(p.pn)]
    if len(todo) < 2:
        return
    got = _pool_ocr([p.page for p in todo])
    if got is None:
        return
    by: dict = {}
    for p, lines in zip(todo, got):
        by.setdefault(id(p._ocr), (p._ocr, {}))[1][p.pn] = lines
    for read, part in by.values():
        read.put(part)


def _ocr_words(lines) -> list:
    """OCR 줄을 낱말로 나눈다. 낱말 자리는 줄 안 글자 차례로 어림한다."""
    out = []
    for x0, y0, x1, y1, t in lines:
        n = len(t) or 1
        for m in re.finditer(r"\S+", t):
            out.append((x0 + (x1 - x0) * m.start() / n, y0, x0 + (x1 - x0) * m.end() / n, y1, m.group()))
    return out


class Page:
    def __init__(self, page, kind: str, scan: bool, di: int = 0, pn: int = 1, ocr=None):
        self.page = page
        self.kind = kind
        self.di, self.pn = di, pn       # 몇 번째 파일의 몇 쪽인지(화면이 원본 위에 상자를 그릴 때 씀)
        self.lines = None               # OCR 로 읽은 쪽이면 줄들. 글자층이 있는 쪽은 None
        self._ocr = ocr                 # (쪽, 쪽 번호) -> OCR 줄. 글자층이 없는 쪽을 쓸 때 한 번만 부른다
        # 엔진의 scan 표시는 로고 그림만 있어도 켜진다. 글자층이 있는지는 글자를 직접 보고 정한다
        raw = page.get_text()
        self._set(raw, page.get_text("words") if len(raw.strip()) >= 50 else [])

    def _set(self, raw: str, words: list) -> None:
        self.has_text = len(raw.strip()) >= 50
        self.up = raw.upper()
        self.code = _norm_code(raw)
        self.text = _norm_text(raw)
        self.words = words
        self.wordset = {re.sub(r"[^0-9A-Z]", "", w[4].upper()) for w in self.words}

    def read_ocr(self) -> bool:
        """글자층이 없는 쪽이면 OCR 글자로 채운다. 견줄 글자가 있으면 True."""
        if self.has_text or self._ocr is None:
            return self.has_text
        read, self._ocr = self._ocr, None
        try:
            lines = read(self.page, self.pn) or []
        except Exception:
            lines = []
        raw = "\n".join(ln[4] for ln in lines)
        if len(raw.strip()) >= 50:
            self.lines = lines
            self._set(raw, _ocr_words(lines))
        return self.has_text

    def find(self, text: str, clip=None) -> list:
        """글이 찍힌 자리들. 글자층은 search_for, OCR 쪽은 그 글을 품은 줄의 자리."""
        if self.lines is None:
            try:
                return list(self.page.search_for(text, clip=clip) if clip is not None else self.page.search_for(text))
            except Exception:
                return []
        q = _norm_code(text)
        if not q:
            return []
        import pymupdf
        return [pymupdf.Rect(x0, y0, x1, y1) for x0, y0, x1, y1, t in self.lines
                if (clip is None or clip[1] <= (y0 + y1) / 2 <= clip[3]) and q in _norm_code(t)]

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


def load_pages(docs, meta, ocr: bool = True) -> list[Page]:
    """docs: [(파일 이름, pymupdf 문서)], meta: 엔진의 쪽 정보(payload pages: file, no, kind, scan).
    ocr 을 끄면 글자층이 없는 쪽은 견주지 않는다."""
    kind = {(m.get("file"), m.get("no")): (m.get("kind") or "", bool(m.get("scan"))) for m in meta or []}
    out = []
    for di, (name, doc) in enumerate(docs):
        if doc is None:
            continue
        read = cached_ocr(getattr(doc, "name", None)) if ocr else None
        for i, pg in enumerate(doc, 1):
            k, scan = kind.get((name, i), ("", False))
            out.append(Page(pg, k, scan, di, i, read))
    return out


# 원본 위에 상자로 보여 줄 칸. 화면은 줄을 고르면 이 칸들의 자리를 모두 그리고, 고른 칸의 자리를 진하게 그린다
LOC_COLS = ("Purchase Order", "Material", "Vendor Part No.", "품명", "B/L quantity", "단위", "Net Price", "Invoice amount")


def _search(p, text, clip=None) -> list:
    return p.find(text, clip)


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
        v = _val(row, c)
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
        hits = [(p, r) for p in pages for r in p.find(v)]
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
    for p in same or pages:
        p.read_ocr()                    # 그 서류가 스캔이면 OCR 글자로 견준다(한 쪽에 한 번)
    # 그 서류(인보이스, 포장명세서)에 견줄 글자가 없다. 다른 서류의 글자로 대신 찾으면 「못 찾음」이 쏟아진다
    mine = [p for p in same if p.has_text] if same else [p for p in pages if p.has_text]
    text_pages = [p for p in pages if p.has_text]
    st, hint = {}, {}
    if not mine:
        return {c: ("-" if not _val(row, c) else "?") for c in CHECK_COLS}, hint
    at = _anchor(mine, row, seen)
    if loc is not None:
        loc.update(locate(row, at, mine))
    near = at[0].band(at[1], 0.8) if at else []
    wide = at[0].band(at[1], 3.0) if at else []
    for c in CHECK_COLS:
        v = _val(row, c)
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
    # 🔴 빈 공급사 부품 번호에 그 줄의 일련번호를 넣은 정답이 있다(HDLCA125295, 수리 반송). 같은 꼴인데 비워 둔 정답도
    #    있어(PNS26081813) 규칙으로 채우지는 않고 사람이 보게만 한다. 정답 97건과 작업 199건에서 이 꼴은 둘뿐이었다(11/15)
    serial = _val(row, "Serial No.")
    if st.get("Vendor Part No.") == "-" and serial:
        hint["Vendor Part No."] = serial
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


def check_items(items: list, docs, meta, ocr: bool = True) -> dict:
    """품목 줄마다 _ok(칸별 상태), _hint(빈 칸에 넣을 만한 원본 값), _at(칸별 원본 자리)를 붙인다. 모은 수를 낸다.
    _hl(줄을 고르면 원본에 그릴 상자)도 _at 으로 바꾼다. 엔진이 붙인 _hl 은 값을 묶음 첫 쪽부터 찾아 엉뚱한 자리가 많았다."""
    pages = load_pages(docs, meta, ocr)
    if ocr:
        _prefetch(items, pages)
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
