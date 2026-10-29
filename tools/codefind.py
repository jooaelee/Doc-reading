# -*- coding: utf-8 -*-
"""서류에 적힌 이름으로 KEROL 코드를 찾는다. 모델을 부르지 않는다.

9월 10일 실무 PM 회의에서 어려운 자리로 꼽힌 둘 가운데 하나다. 서류에는 이름이 적혀 있고
KEROL 에는 코드가 있다. 대소문자와 띄어쓰기와 법인격 표기가 조금씩 달라서 글자 그대로는
못 맞춘다. 같은 이름에 코드가 둘인 경우도 있어서 주소를 같이 본다.

푸는 방법은 셋을 겹친다.

  1. 확정 매핑    담당자가 한 번 골라 준 짝을 저장해 두고 먼저 본다. 회의에서 말한 "학습"이다.
  2. 정규화 일치  법인격과 문장 부호를 걷어낸 뒤 같으면 그대로 쓴다.
  3. 닮은 것 찾기 글자 세 개씩 잘라 만든 벡터의 코사인 값. 주소가 있으면 주소도 같이 센다.

셋 다 규칙과 산수라서 LLM 을 부르지 않는다. 못 찾으면 빈손으로 두고 사람에게 넘긴다.
값을 지어내지 않는다.

쓰는 법

  python3 tools/codefind.py --master 거래처.csv --query "L&F CO., LTD."
  python3 tools/codefind.py --master 거래처.csv --query "..." --address "SEOUL KOREA" --top 3
  python3 tools/codefind.py --master 거래처.csv --learn "내덱" NDCC001      # 확정 매핑 저장
  python3 tools/codefind.py --eval 거래처.csv                              # 표기 흔들림에 얼마나 견디는지
  python3 tools/codefind.py --eval-kerol                                   # 저장소의 KEROL 이름으로 같은 시험

거래처 파일은 CSV(code,name,address) 나 JSON([{"code","name","address"}]) 이다. KEROL 의
Company 화면과 Customer Company 화면에서 받아 만든다. 실제 거래처 파일은 저장소에 넣지 않는다.
"""
import argparse, csv, json, math, os, re, sys, time, unicodedata
from collections import defaultdict

# ---------- 정규화 ----------
# 법인격 표기. 회사 이름의 핵심이 아니라 뒤에 붙는 꼬리라서 걷어낸다.
LEGAL = [
    "CO LTD", "CO LTD.", "COLTD", "CO., LTD", "COMPANY LIMITED", "COMPANY", "LIMITED", "LTD",
    "INCORPORATED", "INC", "CORPORATION", "CORP", "GMBH", "AKTIENGESELLSCHAFT", "AG",
    "SOCIETE ANONYME", "SA", "SPA", "SRL", "BV", "NV", "KFT", "ZRT", "SP Z OO", "SDN BHD",
    "PTE", "PVT", "PTY", "LLC", "LLP", "PLC", "JSC", "OOO", "AS", "AB", "OY", "KK",
    "CO", "GROUP", "HOLDINGS", "HOLDING", "INTERNATIONAL", "INTL",
]
LEGAL = sorted(set(LEGAL), key=len, reverse=True)
_LEGAL_RE = re.compile(r"\b(" + "|".join(re.escape(x) for x in LEGAL) + r")\b")
_PUNCT_RE = re.compile(r"[.,()\[\]{}'\"`/\\:;!?*#|_+~^<>]")


def normalize(s):
    """대소문자와 특수 공백과 문장 부호를 고른다. 글자는 지우지 않는다."""
    if not s: return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace(" ", " ").replace("　", " ").replace("&", " AND ")
    s = _PUNCT_RE.sub(" ", s).replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def core(s):
    """법인격 꼬리까지 걷어낸 알맹이. 빈 문자열이 되면 정규화한 것을 그대로 쓴다."""
    n = normalize(s)
    c = re.sub(r"\s+", " ", _LEGAL_RE.sub(" ", n)).strip()
    return c or n


def ngrams(s, n=3):
    s = " " + s.replace(" ", " ") + " "
    return [s[i:i + n] for i in range(max(len(s) - n + 1, 1))]


def tokens(s):
    return [t for t in core(s).split() if t]


# ---------- 닮은 정도 ----------
def _vec(text, idf, n=3):
    tf = defaultdict(float)
    for g in ngrams(text, n): tf[g] += 1.0
    v = {g: (1.0 + math.log(c)) * idf.get(g, idf["__default__"]) for g, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {g: x / norm for g, x in v.items()}


def _cos(a, b):
    if len(a) > len(b): a, b = b, a
    return sum(x * b.get(g, 0.0) for g, x in a.items())


def _cover(q, d):
    """질의의 글자 조각이 상대 쪽에 얼마나 들어 있는지. 짧은 질의가 긴 이름 안에 있을 때를 위한 것이다.

    코사인은 길이가 많이 다르면 값이 눌린다. 주소를 "CHANGWON KOREA" 처럼 줄여 적은 경우가 그렇다.
    """
    if not q: return 0.0
    tot = sum(x * x for x in q.values()) or 1.0
    return sum(x * x for g, x in q.items() if g in d) / tot


def _sim(q, d, min_grams=6):
    """코사인과 덮임 중 큰 값. 질의가 너무 짧으면 덮임은 쓰지 않는다. 아무 이름에나 붙기 때문이다."""
    c = _cos(q, d)
    return max(c, 0.9 * _cover(q, d)) if len(q) >= min_grams else c


def _jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb: return 0.0
    return len(sa & sb) / len(sa | sb)


class Candidate:
    __slots__ = ("code", "name", "address", "score", "why")

    def __init__(self, code, name, address, score, why):
        self.code, self.name, self.address, self.score, self.why = code, name, address, score, why

    def as_dict(self):
        return {"code": self.code, "name": self.name, "address": self.address,
                "score": round(self.score, 4), "why": self.why}

    def __repr__(self):
        return f"<{self.code} {self.score:.3f} {self.why} {self.name[:34]}>"


# ---------- 색인 ----------
class CodeIndex:
    """거래처 목록을 받아 두고 이름으로 코드를 찾는다.

    entries: [{"code": "...", "name": "...", "address": "..."}]
    learned: 담당자가 확정한 짝을 담은 파일 경로. 없으면 저장하지 않는다.
    """

    NAME_W, ADDR_W = 0.75, 0.25          # 주소가 양쪽에 있을 때의 가중치
    NGRAM_W, TOKEN_W = 0.7, 0.3          # 글자 조각과 낱말의 가중치
    SUGGEST = 0.55                        # 이보다 낮으면 후보로도 내지 않는다
    CONFIDENT = 0.82                      # 이보다 높고 2등과 벌어지면 자동으로 쓴다
    GAP = 0.06                            # 1등과 2등의 차이가 이보다 작으면 애매하다고 본다
    ADDR_GAP = 0.08                       # 이름이 같을 때 주소 점수가 이만큼 벌어지면 가른다

    def __init__(self, entries, learned=None):
        self.entries = [dict(e) for e in entries if e.get("code") and e.get("name")]
        self.learned_path = learned
        self.learned = {}
        if learned and os.path.exists(learned):
            with open(learned, encoding="utf-8") as f: self.learned = json.load(f)
        self._build()

    def _build(self):
        docs = [core(e["name"]) for e in self.entries]
        addrs = [normalize(e.get("address") or "") for e in self.entries]
        self.by_core = defaultdict(list)
        for i, c in enumerate(docs): self.by_core[c].append(i)

        df = defaultdict(int)
        for d in docs:
            for g in set(ngrams(d)): df[g] += 1
        N = max(len(docs), 1)
        self.idf = {g: math.log(1.0 + N / c) for g, c in df.items()}
        self.idf["__default__"] = math.log(1.0 + N)
        self.vecs = [_vec(d, self.idf) for d in docs]
        self.toks = [tokens(e["name"]) for e in self.entries]

        adf = defaultdict(int)
        for a in addrs:
            for g in set(ngrams(a)): adf[g] += 1
        self.aidf = {g: math.log(1.0 + N / c) for g, c in adf.items()}
        self.aidf["__default__"] = math.log(1.0 + N)
        self.avecs = [_vec(a, self.aidf) if a else None for a in addrs]

    # -- 확정 매핑 --
    @staticmethod
    def _key(name, address=None):
        k = core(name)
        return k + ("|" + normalize(address) if address else "")

    def learn(self, name, code, address=None, who=None):
        """담당자가 고른 짝을 저장한다. 다음에 같은 이름이 오면 이것부터 본다."""
        k = self._key(name, address)
        rec = self.learned.get(k) or {"code": code, "count": 0}
        rec["code"], rec["count"] = code, rec.get("count", 0) + 1
        rec["updated"] = time.strftime("%Y-%m-%d")
        if who: rec["who"] = who
        self.learned[k] = rec
        if self.learned_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.learned_path)), exist_ok=True)
            with open(self.learned_path, "w", encoding="utf-8") as f:
                json.dump(self.learned, f, ensure_ascii=False, indent=1, sort_keys=True)
        return rec

    def _entry(self, code):
        for e in self.entries:
            if e["code"] == code: return e
        return {"code": code, "name": "", "address": ""}

    # -- 찾기 --
    def lookup(self, name, address=None, top=3):
        """닮은 순으로 후보를 낸다. 확정 매핑이 있으면 그것만 낸다."""
        if not name or not str(name).strip(): return []

        for k in ([self._key(name, address)] if address else []) + [self._key(name)]:
            rec = self.learned.get(k)
            if rec:
                e = self._entry(rec["code"])
                return [Candidate(rec["code"], e.get("name", ""), e.get("address", ""), 1.0,
                                  f"확정 매핑 {rec.get('count', 1)}회")]

        c = core(name)
        exact = self.by_core.get(c, [])
        if len(exact) == 1:
            e = self.entries[exact[0]]
            return [Candidate(e["code"], e["name"], e.get("address", ""), 0.99, "이름 일치")]

        qv = _vec(c, self.idf)
        qt = tokens(name)
        qa = _vec(normalize(address), self.aidf) if address else None

        # 이름이 똑같은 거래처가 여럿이면 주소로만 가른다. 사람이 하는 것과 같은 순서다.
        if len(exact) > 1 and qa is not None:
            scored = [(self._addr_sim(qa, i), i) for i in exact]
            scored.sort(key=lambda x: -x[0])
            if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= self.ADDR_GAP):
                out = []
                for s, i in scored[:top]:
                    e = self.entries[i]
                    out.append(Candidate(e["code"], e["name"], e.get("address", ""),
                                         self.CONFIDENT + 0.05 if (s, i) == scored[0] else 0.6, "주소로 가름"))
                return out

        out = []
        for i, e in enumerate(self.entries):
            s = self.NGRAM_W * _sim(qv, self.vecs[i]) + self.TOKEN_W * _jaccard(qt, self.toks[i])
            why = "닮은 이름"
            if qa is not None and self.avecs[i] is not None:
                s = self.NAME_W * s + self.ADDR_W * self._addr_sim(qa, i)
                why = "이름과 주소"
            if i in exact:
                s = max(s, 0.9)
                why = "이름 일치(여럿)"
            if s >= self.SUGGEST:
                out.append(Candidate(e["code"], e["name"], e.get("address", ""), s, why))
        out.sort(key=lambda x: (-x.score, x.code))
        return out[:top]

    def _addr_sim(self, qa, i):
        av = self.avecs[i]
        return _sim(qa, av) if av is not None else 0.0

    def decide(self, name, address=None, top=3):
        """후보와 함께 어떻게 해야 하는지를 낸다. 화면과 파이프라인이 이 값을 본다."""
        cands = self.lookup(name, address, top=top)
        if not cands:
            return {"status": "없음", "code": None, "candidates": [],
                    "message": "닮은 거래처가 없습니다. 신규이거나 표기가 많이 다릅니다."}
        best = cands[0]
        second = cands[1] if len(cands) > 1 else None
        ambiguous = second is not None and (best.score - second.score) < self.GAP
        if best.score >= self.CONFIDENT and not ambiguous:
            return {"status": "확정", "code": best.code, "candidates": [c.as_dict() for c in cands],
                    "message": f"{best.code} 으로 채웁니다."}
        if ambiguous and core(best.name) == core(second.name):
            return {"status": "선택", "code": None, "candidates": [c.as_dict() for c in cands],
                    "message": "이름이 같은 거래처가 여럿입니다. 주소를 보고 담당자가 고릅니다."}
        return {"status": "추천", "code": None, "candidates": [c.as_dict() for c in cands],
                "message": "담당자가 확인하고 고릅니다."}


# ---------- 거래처 파일 ----------
def load_master(path):
    if str(path).lower().endswith(".json"):
        with open(path, encoding="utf-8") as f: d = json.load(f)
        return d["entries"] if isinstance(d, dict) else d
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        code = low.get("code") or low.get("company_cd") or low.get("cust_cd") or low.get("거래처코드")
        name = low.get("name") or low.get("company_nm") or low.get("cust_nm") or low.get("거래처명")
        addr = low.get("address") or low.get("addr") or low.get("주소") or ""
        if code and name: out.append({"code": code, "name": name, "address": addr})
    return out


# ---------- 표기 흔들림 시험 ----------
def variants(name):
    """서류에서 실제로 보이는 표기 흔들림. (이름, 어떤 흔들림인지)"""
    n = name
    v = [
        (n.lower(), "소문자"),
        (n.upper(), "대문자"),
        (re.sub(r"[.,]", "", n), "문장 부호 없음"),
        (n.replace(" ", " "), "특수 공백"),
        (re.sub(r"\s+", "  ", n), "겹친 공백"),
        (n.replace("&", " AND "), "기호를 낱말로"),
        (re.sub(r"(?i)\bCO\.?,?\s*LTD\.?", "CO.,LTD", n), "법인격 붙여 쓰기"),
        (re.sub(r"(?i)[,.]?\s*\b(CO\.?,?\s*LTD\.?|INC\.?|CORP\.?|LIMITED|GMBH|KFT)\b\.?", "", n).strip(), "법인격 없음"),
        (" ".join(n.split()[:3]), "앞 세 낱말"),
        (n + " (SHANGHAI)", "지점 덧붙임"),
    ]
    seen, out = set(), []
    for s, why in v:
        s = s.strip()
        if s and s not in seen:
            seen.add(s); out.append((s, why))
    return out


def evaluate(entries, learned=None, top=3):
    """거래처 목록으로 흔들린 이름을 만들어 얼마나 찾는지 센다."""
    idx = CodeIndex(entries, learned=learned)
    per = defaultdict(lambda: [0, 0, 0])      # 흔들림별 [시도, 1등 맞음, 3등 안에 맞음]
    total = [0, 0, 0]
    t0 = time.perf_counter()
    for e in entries:
        for q, why in variants(e["name"]):
            cands = idx.lookup(q, e.get("address") or None, top=top)
            codes = [c.code for c in cands]
            hit1 = bool(codes) and codes[0] == e["code"]
            hitk = e["code"] in codes
            for acc in (per[why], total):
                acc[0] += 1; acc[1] += int(hit1); acc[2] += int(hitk)
    ms = (time.perf_counter() - t0) * 1000
    return {"entries": len(entries), "queries": total[0], "top1": total[1], "topk": total[2],
            "ms": ms, "ms_per_query": ms / max(total[0], 1), "per": dict(per), "top": top}


def kerol_entries(root=None):
    """저장소에 있는 KEROL 배터리 데이터의 거래처 이름으로 시험용 목록을 만든다.

    이름은 저장소에 이미 있는 값이고 코드는 시험용으로 붙인 가짜다. KEROL 의 거래처 코드
    마스터를 받으면 그것으로 바꾼다.
    """
    import sqlite3
    import wuxi_paths
    db = os.path.join(root, "kerol.sqlite") if root else os.path.join(wuxi_paths.DATA, "kerol.sqlite")
    con = sqlite3.connect(db)
    names = []
    for col, tbl in (("shipper", "portal_grid"), ("consignee", "portal_grid"), ("seller", "data_analysis")):
        names += [r[0] for r in con.execute(
            f"select distinct {col} from {tbl} where {col} is not null and {col} != ''")]
    con.close()
    seen, out = set(), []
    for nm in sorted(set(names)):
        c = core(nm)
        if c in seen: continue
        seen.add(c)
        out.append({"code": "T%04d" % (len(out) + 1), "name": nm, "address": ""})
    return out


def main():
    ap = argparse.ArgumentParser(description="이름으로 KEROL 코드를 찾습니다. 모델을 부르지 않습니다.")
    ap.add_argument("--master", help="거래처 파일 (CSV code,name,address 또는 JSON)")
    ap.add_argument("--query", help="서류에 적힌 이름")
    ap.add_argument("--address", help="서류에 적힌 주소")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--learned", help="확정 매핑 파일 (JSON). 없으면 만든다")
    ap.add_argument("--learn", nargs=2, metavar=("이름", "코드"), help="확정 매핑을 저장한다")
    ap.add_argument("--eval", metavar="거래처파일", help="표기 흔들림 시험")
    ap.add_argument("--eval-kerol", action="store_true", help="저장소의 KEROL 이름으로 같은 시험")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로")
    a = ap.parse_args()

    if a.eval or a.eval_kerol:
        entries = kerol_entries() if a.eval_kerol else load_master(a.eval)
        r = evaluate(entries, learned=a.learned, top=a.top)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1)); return 0
        print(f"거래처 {r['entries']}개, 흔들린 이름 {r['queries']}개")
        print(f"1등 일치 {r['top1']}/{r['queries']} ({100 * r['top1'] / r['queries']:.1f}%), "
              f"{r['top']}등 안 {r['topk']}/{r['queries']} ({100 * r['topk'] / r['queries']:.1f}%), "
              f"질의당 {r['ms_per_query']:.2f}ms")
        print()
        print(f"{'흔들림':16} {'시도':>5} {'1등':>7} {'3등 안':>8}")
        for why, (n, h1, hk) in sorted(r["per"].items(), key=lambda x: x[1][1] / max(x[1][0], 1)):
            print(f"{why:16} {n:5} {100 * h1 / n:6.1f}% {100 * hk / n:7.1f}%")
        return 0

    if not a.master:
        ap.error("--master 가 필요합니다")
    idx = CodeIndex(load_master(a.master), learned=a.learned)

    if a.learn:
        rec = idx.learn(a.learn[0], a.learn[1], a.address)
        print(f"저장했습니다. {a.learn[0]} → {rec['code']} ({rec['count']}회)")
        return 0

    if not a.query:
        ap.error("--query 나 --learn 가 필요합니다")
    d = idx.decide(a.query, a.address, top=a.top)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1)); return 0
    print(f"[{d['status']}] {d['message']}")
    for i, c in enumerate(d["candidates"], 1):
        print(f"  {i}. {c['code']:12} {c['score']:.3f}  {c['why']:12} {c['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
