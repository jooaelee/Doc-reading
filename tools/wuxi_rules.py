"""우시 판독 규칙. 엔진이 판독할 때 읽고, 운영 화면(/admin)이 고친다.

규칙의 종류 여섯
  header    열 이름      칸마다 서류 표의 열 이름을 더한다. 판독 지침(프롬프트)에 들어간다
  code      코드 사전    품명이나 부품 번호가 같은 줄의 SAP 코드, 자재 코드, 원산지 같은 값을 채우거나 고친다. 판독 뒤 후처리
  alias     값 바꾸기    단위나 원산지 표기를 바꾼다(PCS. → PCS, JAPAN → JP). 판독 뒤 후처리
  guide     판독 지침    공급사 서류의 특징을 AI 에게 알린다. 프롬프트
  check     빠짐 검사    근거 네 가지를 켜고 끈다. 금액 허용 차이
  required  필수 칸      비면 주의로 표시할 G-ERP 열

범위는 공통(*) 또는 공급사 하나다. 같은 자리에 둘 다 있으면 공급사 규칙이 이긴다.
단계는 후보(candidate) → 반영(active) 이고, 공급사 규칙을 공통으로 올리는 것을 격상이라 부른다. 꺼짐(off), 거절(rejected)도 있다.

저장
  기본값   저장소 schema/rules_default.json. Git 으로 같이 배포된다
  운영 값  WUXI_HOME/rules.json. 화면에서 고친 것과 학습 후보. 기본값과 id 가 같으면 운영 값이 이긴다
  이력     WUXI_HOME/rules_history.jsonl. 누가, 언제, 무엇을, 왜

🔴 열 이름의 기본값은 레코드로 두지 않는다. docpipe.HYNIX_RULES 프롬프트에 이미 적혀 있고, 거기서 빼면 시험을 마친 판독이
   달라진다. 그래서 화면에는 읽기 전용으로 보여 주고, 규칙은 그 위에 더하는 것만 받는다.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402

COMMON = "*"
KINDS = {"header": "열 이름", "code": "코드 사전", "alias": "값 바꾸기", "guide": "판독 지침",
         "check": "빠짐 검사", "required": "필수 칸"}
STATUS = {"candidate": "후보", "active": "반영", "off": "꺼짐", "rejected": "거절", "deleted": "삭제"}
EVENTS = {"candidate": "후보 생김", "activate": "새로 반영", "promote": "격상", "off": "해제", "edit": "수정",
          "reject": "거절", "delete": "삭제", "create": "새로 반영", "import": "가져오기", "supplier": "공급사",
          "settings": "설정"}

# 판독 칸 이름과 화면 이름
FIELDS = {
    "sap_code": "SAP 코드", "material_code": "자재 코드", "po_no": "PO 번호", "po_item_no": "PO 품목 번호",
    "iv_no": "SK Hynix I/V 번호", "hios_no": "HIOS 번호", "part_no": "부품 번호", "serial_no": "시리얼 번호",
    "description": "품명", "qty": "수량", "unit": "단위", "unit_price": "단가", "amount": "금액",
    "hs_code": "HS 코드", "country_of_origin": "원산지", "case_no": "케이스 번호", "net": "순중량", "gross": "총중량",
}
CODE_FIELDS = ("sap_code", "material_code", "hios_no", "part_no", "country_of_origin", "hs_code", "unit")
HEADER_FIELDS = ("sap_code", "po_no", "po_item_no", "iv_no", "hios_no", "material_code", "serial_no", "part_no",
                 "description", "qty", "unit", "unit_price", "amount", "hs_code", "country_of_origin", "case_no",
                 "net", "gross")
ALIAS_FIELDS = ("unit", "country_of_origin")

# docpipe.HYNIX_RULES 에 적힌 열 이름(읽기 전용 기본값). 프롬프트를 바꾸면 여기도 같이 바꾼다
BUILTIN_HEADERS = {
    "sap_code": ["Sap Code", "SAP Code", "物料代码 / SKhynix SAP Code(통관표)"],
    "po_no": ["Po No.", "PO No.", "Purchase Order"],
    "po_item_no": ["Po Item No.", "PO Item"],
    "iv_no": ["SK Hynix I/V No.", "I/V No."],
    "hios_no": ["HIOS No."],
    "material_code": ["Material", "Material No", "Material Code", "Item Code"],
    "serial_no": ["Serial No", "SERIAL NUMBER", "S/N"],
    "part_no": ["Part No.", "Vendor Part No.", "Model", "Article No."],
}

CHECKS = {
    "amount": ("금액 합계", "인보이스마다 줄 금액의 합이 인쇄된 총액과 같은지"),
    "lines_pl": ("포장명세서 줄 수", "인보이스 줄 수와 포장명세서 줄 수가 같은지"),
    "lines_cn": ("통관표 줄 수", "인보이스 줄 수와 통관 용도설명서 줄 수가 같은지"),
    "pages": ("쪽마다 읽은 줄", "글자가 있는 인보이스 쪽마다 읽은 줄이 하나 이상 확인되는지"),
}
GERP_COLUMNS = ["Item", "Purchase Order", "Material", "Vendor Part No.", "B/L quantity", "Invoice amount", "Net Price"]

DEFAULT_FILE = os.path.join(wuxi_paths.SCHEMA, "rules_default.json")
_lock = threading.RLock()
_cache: dict = {"key": None, "state": None}


# ───────────────────────────────────────── 글자 맞추기
def norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().upper()


def norm_desc(v) -> str:
    """품명 비교용. 기호와 띄어쓰기 차이는 같은 것으로 본다."""
    return re.sub(r"[^0-9A-Z가-힣一-鿿]+", " ", norm(v)).strip()


def slug(name: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "-", norm(name).lower()).strip("-")
    return (s[:40] or "supplier-" + secrets.token_hex(3))


# ───────────────────────────────────────── 저장
def _paths():
    return wuxi_paths.work("rules.json"), wuxi_paths.work("rules_history.jsonl")


def _read_json(path, empty):
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
            return v if isinstance(v, dict) else copy.deepcopy(empty)
    except (OSError, ValueError):
        return copy.deepcopy(empty)


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _load_raw():
    live, _ = _paths()
    key = (_mtime(DEFAULT_FILE), _mtime(live), live)
    if _cache["key"] == key and _cache["state"] is not None:
        return _cache["state"]
    base = _read_json(DEFAULT_FILE, {"rules": [], "suppliers": {}})
    cur = _read_json(live, {"rules": [], "suppliers": {}})
    rules = {}
    for r in base.get("rules") or []:
        if isinstance(r, dict) and r.get("id"):
            rr = dict(r)
            rr.setdefault("source", "default")
            rules[rr["id"]] = rr
    for r in cur.get("rules") or []:
        if isinstance(r, dict) and r.get("id"):
            rules[r["id"]] = r
    sups = dict(base.get("suppliers") or {})
    sups.update(cur.get("suppliers") or {})
    st = {"rules": rules, "suppliers": sups}
    _cache.update(key=key, state=st)
    return st


def _save(st):
    live, _ = _paths()
    os.makedirs(os.path.dirname(live), exist_ok=True)
    out = {"version": 1, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
           "rules": list(st["rules"].values()), "suppliers": st["suppliers"]}
    tmp = live + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, live)
    _cache.update(key=None, state=None)


def _history(event, rule=None, by="", reason="", extra=None):
    _, hp = _paths()
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(), "event": event, "by": by or "관리자",
           "reason": (reason or "")[:300]}
    if rule:
        row.update(rule=rule["id"], kind=rule["kind"], scope=rule["scope"], text=describe(rule),
                   status=rule["status"])
    if extra:
        row.update(extra)
    with open(hp, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def history(limit=300, event=None, rule_id=None):
    _, hp = _paths()
    rows = []
    try:
        with open(hp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    if event:
        rows = [r for r in rows if r.get("event") in event]
    if rule_id:
        rows = [r for r in rows if r.get("rule") == rule_id]
    return list(reversed(rows))[:limit]


# ───────────────────────────────────────── 공급사
def suppliers() -> dict:
    with _lock:
        return copy.deepcopy(_load_raw()["suppliers"])


def supplier_name(sid) -> str:
    if not sid or sid == COMMON:
        return "공통"
    return (suppliers().get(sid) or {}).get("name") or sid


def save_supplier(name: str, match=None, note="", sid=None, by="", source="manual") -> dict:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not name:
        raise ValueError("공급사 이름을 넣어 주십시오.")
    with _lock:
        st = _load_raw()
        sid = sid or slug(name)
        cur = st["suppliers"].get(sid) or {"id": sid, "created": time.time(), "source": source}
        words = [w for w in (match if match is not None else cur.get("match") or [name]) if str(w).strip()]
        cur.update(name=name, match=sorted({norm(w) for w in words if len(norm(w)) >= 3}),
                   note=str(note or cur.get("note") or "")[:300], updated=time.time())
        st["suppliers"][sid] = cur
        _save(st)
    what = "처음 본 송하인으로 공급사 「{}」 정보를 만들었습니다" if source == "auto" else "공급사 「{}」 정보를 저장했습니다"
    _history("supplier", by=by, extra={"scope": sid, "text": what.format(name) + f". 알아보는 낱말 {len(cur['match'])}개"})
    return cur


def identify_supplier(text: str = "", shipper: str = ""):
    """서류 글이나 판독한 송하인 이름으로 공급사를 고른다. 가장 긴 낱말이 맞는 공급사가 이긴다."""
    t = norm(text)[:20000]
    sh = norm(shipper)
    best, best_len = None, 0
    for sid, s in suppliers().items():
        for w in s.get("match") or []:
            if len(w) > best_len and ((sh and (w in sh or sh in w)) or (w and w in t)):
                best, best_len = sid, len(w)
    return best


def supplier_for(text: str, ex: dict | None = None, auto_create=True):
    """판독 전에는 글로, 판독 뒤에는 송하인 이름까지 보고 공급사를 정한다. 처음 보는 송하인은 공급사로 새로 적는다."""
    shipper = str((ex or {}).get("shipper_name") or "").strip()
    sid = identify_supplier(text, shipper)
    if sid or not (auto_create and shipper and len(norm(shipper)) >= 3):
        return sid
    return save_supplier(shipper, [shipper], by="자동", source="auto")["id"]


# ───────────────────────────────────────── 규칙 읽기
def all_rules(include_deleted=False) -> list:
    with _lock:
        rs = [copy.deepcopy(r) for r in _load_raw()["rules"].values()]
    if not include_deleted:
        rs = [r for r in rs if r.get("status") != "deleted"]
    return sorted(rs, key=lambda r: (r.get("kind", ""), r.get("scope", ""), -(r.get("updated") or 0)))


def get(rid: str) -> dict | None:
    with _lock:
        r = _load_raw()["rules"].get(rid)
        return copy.deepcopy(r) if r else None


def active(kind: str, supplier=None) -> list:
    """그 자리에 쓰이는 규칙. 공급사 규칙이 앞에 온다(먼저 맞는 것이 이긴다)."""
    rs = [r for r in all_rules() if r["kind"] == kind and r["status"] == "active"
          and r["scope"] in ((supplier, COMMON) if supplier else (COMMON,))]
    return sorted(rs, key=lambda r: (r["scope"] == COMMON, -(r.get("updated") or 0)))


def key_of(kind, data) -> tuple:
    d = data or {}
    if kind == "code":
        return (d.get("on") or "desc", norm_desc(d.get("key")), d.get("field"))
    if kind == "alias":
        return (d.get("field"), norm(d.get("from")))
    if kind == "header":
        return (d.get("field"), norm(d.get("header")))
    if kind == "guide":
        return (norm(d.get("text"))[:200],)
    if kind == "check":
        return (d.get("check"),)
    if kind == "required":
        return tuple(d.get("columns") or [])
    return (json.dumps(d, sort_keys=True),)


def find_same(kind, scope, data, statuses=("candidate", "active", "off")):
    k = key_of(kind, data)
    for r in all_rules():
        if r["kind"] == kind and r["scope"] == scope and r["status"] in statuses and key_of(kind, r["data"]) == k:
            return r
    return None


def josa(word: str, pair: str) -> str:
    """받침을 보고 조사를 고른다. pair 는 "을를", "이가", "은는", "과와", "으로로" 가운데 하나."""
    w = str(word or "").strip()
    ch = w[-1] if w else ""
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        if pair == "으로로":
            return "로" if jong in (0, 8) else "으로"     # ㄹ 받침은 「로」
        return pair[0] if jong else pair[1]
    return {"을를": "를", "이가": "가", "은는": "는", "과와": "와", "으로로": "로"}[pair]


def describe(r: dict) -> str:
    """화면과 이력에 쓰는 규칙 문장(합쇼체). 따옴표 안의 값 뒤에는 「값」, 「열」 같은 낱말을 붙여 조사가 어긋나지 않게 한다."""
    d, k = r.get("data") or {}, r.get("kind")
    f = FIELDS.get(d.get("field"), d.get("field") or "")
    if k == "code":
        verb = "채웁니다" if (d.get("mode") or "fill") == "fill" else "바꿉니다"
        if (d.get("on") or "desc") == "part":
            where = f"부품 번호가 「{d.get('key')}」인 줄"
        elif d.get("match") == "contains":
            where = f"품명에 「{d.get('key')}」 글자가 들어간 줄"
        else:
            where = f"품명이 「{d.get('key')}」인 줄"
        return f"{where}의 {f}{josa(f, '을를')} 「{d.get('value')}」 값으로 {verb}"
    if k == "alias":
        return f"{f} 「{d.get('from')}」 값을 「{d.get('to')}」 값으로 바꿉니다"
    if k == "header":
        return f"표의 「{d.get('header')}」 열을 {f}{josa(f, '으로로')} 읽습니다"
    if k == "guide":
        return d.get("label") or d.get("text") or ""
    if k == "check":
        name = CHECKS.get(d.get("check"), (d.get("check"), ""))[0]
        if not d.get("enabled", True):
            return f"빠짐 검사에서 「{name}」 항목을 보지 않습니다"
        tol = f"(허용 차이 {d.get('tolerance')})" if d.get("check") == "amount" and d.get("tolerance") is not None else ""
        return f"빠짐 검사에서 「{name}」 항목을 봅니다{tol}"
    if k == "required":
        cols = d.get("columns") or []
        verb = "주의로 표시합니다" if d.get("required", True) else "주의로 표시하지 않습니다"
        return f"「{' 또는 '.join(cols)}」 열이 비면 {verb}"
    return json.dumps(d, ensure_ascii=False)


# ───────────────────────────────────────── 규칙 고치기
def _valid(kind, data) -> dict:
    d = dict(data or {})
    if kind not in KINDS:
        raise ValueError("규칙 종류가 맞지 않습니다.")
    if kind == "code":
        if (d.get("on") or "desc") not in ("desc", "part"):
            raise ValueError("맞출 칸은 품명이나 부품 번호입니다.")
        if d.get("field") not in CODE_FIELDS:
            raise ValueError("채울 칸이 맞지 않습니다.")
        if not str(d.get("key") or "").strip() or not str(d.get("value") or "").strip():
            raise ValueError("맞출 값과 채울 값을 모두 넣어 주십시오.")
        d.update(on=d.get("on") or "desc", match=d.get("match") or "exact", mode=d.get("mode") or "fill",
                 key=str(d["key"]).strip()[:200], value=str(d["value"]).strip()[:120])
    elif kind == "alias":
        if d.get("field") not in ALIAS_FIELDS or not str(d.get("from") or "").strip() or not str(d.get("to") or "").strip():
            raise ValueError("바꿀 칸, 바꿀 값, 바꾼 값을 모두 넣어 주십시오.")
        d.update(**{"from": str(d["from"]).strip()[:60], "to": str(d["to"]).strip()[:60]})
    elif kind == "header":
        if d.get("field") not in HEADER_FIELDS or not str(d.get("header") or "").strip():
            raise ValueError("칸과 열 이름을 모두 넣어 주십시오.")
        d["header"] = re.sub(r"\s+", " ", str(d["header"])).strip()[:80]
    elif kind == "guide":
        if not str(d.get("text") or "").strip():
            raise ValueError("지침을 넣어 주십시오.")
        d["text"] = str(d["text"]).strip()[:1200]
    elif kind == "check":
        if d.get("check") not in CHECKS:
            raise ValueError("검사 이름이 맞지 않습니다.")
        d["enabled"] = bool(d.get("enabled", True))
        if d.get("tolerance") not in (None, ""):
            d["tolerance"] = max(0.0, float(d["tolerance"]))
        else:
            d.pop("tolerance", None)
    elif kind == "required":
        cols = [c for c in (d.get("columns") or []) if c in GERP_COLUMNS]
        if not cols:
            raise ValueError("G-ERP 열을 하나 이상 골라 주십시오.")
        d["columns"] = cols
        d["required"] = bool(d.get("required", True))
    return d


def add(kind, scope, data, status="active", source="manual", by="", reason="", evidence=None) -> dict:
    data = _valid(kind, data)
    scope = scope or COMMON
    with _lock:
        same = find_same(kind, scope, data)
        if same and same["status"] != "candidate":
            raise ValueError("같은 규칙이 이미 있습니다: " + describe(same))
        st = _load_raw()
        now = time.time()
        if same:      # 후보를 사람이 직접 반영하는 경우
            r = st["rules"][same["id"]]
            r.update(data=data, status=status, updated=now)
        else:
            r = {"id": "r_" + secrets.token_hex(5), "kind": kind, "scope": scope, "data": data, "status": status,
                 "source": source, "created": now, "updated": now, "stats": {"applied": 0, "jobs": 0, "last": None},
                 "evidence": list(evidence or [])[:20]}
            st["rules"][r["id"]] = r
        _save(st)
    _history("candidate" if status == "candidate" else "create", r, by, reason)
    return copy.deepcopy(r)


def upsert_candidate(kind, scope, data, evidence: dict, by="학습") -> dict | None:
    """학습이 부른다. 같은 후보가 있으면 근거만 더하고, 이미 반영된 규칙이면 근거를 더한 뒤 None."""
    try:
        data = _valid(kind, data)
    except ValueError:
        return None
    with _lock:
        same = find_same(kind, scope, data, statuses=("candidate", "active", "off", "rejected"))
        st = _load_raw()
        if same:
            r = st["rules"][same["id"]]
            ev = [e for e in r.get("evidence") or [] if not (e.get("job") == evidence.get("job")
                                                            and e.get("row") == evidence.get("row"))]
            r["evidence"] = (ev + [evidence])[-20:]
            r["updated_evidence"] = time.time()
            _save(st)
            return copy.deepcopy(r) if r["status"] == "candidate" else None
    return add(kind, scope, data, status="candidate", source="learned", by=by, evidence=[evidence])


def drop_job_evidence(job: str, kinds, by="학습", again=()) -> list:
    """후보에서 그 작업이 남긴 근거를 걷어 낸다. 근거가 하나도 남지 않은 후보는 지운다. 반영한 규칙은 건드리지 않는다.

    again 은 이번에 다시 나올 후보의 (종류, 공급사, key_of). 그 후보는 근거만 걷고 지우지 않는다(곧 다시 근거가 붙음).
    """
    gone = []
    again = set(again)
    with _lock:
        st = _load_raw()
        hit = False
        for rid, r in st["rules"].items():
            if r.get("status") != "candidate" or r.get("kind") not in kinds:
                continue
            ev = r.get("evidence") or []
            keep = [e for e in ev if e.get("job") != job]
            if len(keep) == len(ev):
                continue
            hit = True
            r = st["rules"][rid] = dict(r, evidence=keep)
            if not keep and (r["kind"], r["scope"], key_of(r["kind"], r["data"])) not in again:
                r.update(status="deleted", updated=time.time())
                gone.append(copy.deepcopy(r))
        if hit:
            _save(st)
    for r in gone:
        _history("delete", r, by, "다시 확정한 값에 이 수정이 없어 근거가 없어진 후보")
    return [r["id"] for r in gone]


def change(rid: str, action: str, by="", reason="", data=None) -> dict:
    """반영, 해제, 거절, 격상, 수정, 삭제. 바뀐 규칙을 돌려준다."""
    with _lock:
        st = _load_raw()
        r = st["rules"].get(rid)
        if not r:
            raise KeyError("규칙을 찾을 수 없습니다.")
        r = st["rules"][rid] = copy.deepcopy(r)
        before = copy.deepcopy(r)
        now = time.time()
        if action == "activate":
            if r["status"] == "active":
                raise ValueError("이미 반영된 규칙입니다.")
            r["status"] = "active"
        elif action == "off":
            r["status"] = "off"
        elif action == "reject":
            if r["status"] != "candidate":
                raise ValueError("후보만 거절할 수 있습니다.")
            r["status"] = "rejected"
        elif action == "promote":
            if r["scope"] == COMMON:
                raise ValueError("이미 공통 규칙입니다.")
            twin = find_same(r["kind"], COMMON, r["data"], statuses=("active",))
            if twin:
                raise ValueError("같은 공통 규칙이 이미 있습니다.")
            r["promoted_from"] = r["scope"]
            r["promoted_at"] = now
            r["scope"] = COMMON
            r["status"] = "active"
        elif action == "edit":
            nd = _valid(r["kind"], {**r["data"], **(data or {})})
            twin = find_same(r["kind"], r["scope"], nd)
            if twin and twin["id"] != rid:
                raise ValueError("같은 규칙이 이미 있습니다.")
            r["data"] = nd
        elif action == "delete":
            if r.get("source") == "default":
                raise ValueError("기본값은 지우지 않고 끕니다.")
            r["status"] = "deleted"
        else:
            raise ValueError("할 수 없는 일입니다.")
        r["updated"] = now
        _save(st)
    ev = {"activate": "activate", "off": "off", "reject": "reject", "promote": "promote", "edit": "edit",
          "delete": "delete"}[action]
    extra = {"before": describe(before)} if action == "edit" else {}
    if action == "promote":
        extra = {"from": before["scope"], "from_name": supplier_name(before["scope"])}
    _history(ev, r, by, reason, extra)
    return copy.deepcopy(r)


def record_applied(trace: list, job_id: str) -> None:
    """판독 한 건에서 쓰인 규칙의 적용 건수를 올린다."""
    if not trace:
        return
    counts = {}
    for t in trace:
        counts[t["rule"]] = counts.get(t["rule"], 0) + 1
    with _lock:
        st = _load_raw()
        now = time.time()
        for rid, n in counts.items():
            r = st["rules"].get(rid)
            if not r:
                continue
            s = r.setdefault("stats", {"applied": 0, "jobs": 0, "last": None})
            s["applied"] = (s.get("applied") or 0) + n
            s["jobs"] = (s.get("jobs") or 0) + 1
            s["last"] = now
            jobs = [j for j in s.get("recent") or [] if j != job_id[:12]]
            s["recent"] = (jobs + [job_id[:12]])[-20:]
        _save(st)


# ───────────────────────────────────────── 엔진이 쓰는 자리
def prompt_addendum(supplier=None) -> str:
    """판독 프롬프트에 덧붙일 글. 기본 지침(HYNIX_RULES) 뒤, "Return only the JSON object." 앞에 들어간다."""
    heads = active("header", supplier)
    guides = active("guide", supplier)
    if not heads and not guides:
        return ""
    out = ["", "Rules added by the operator for these documents:"]
    by_field = {}
    for r in heads:
        by_field.setdefault(r["data"]["field"], []).append(r["data"]["header"])
    for f, hs in by_field.items():
        names = ", ".join(f'"{h}"' for h in dict.fromkeys(hs))
        out.append(f"- {f}: also read it from the column headed {names}, the same way as the headers named above.")
    for r in guides:
        out.append("- " + r["data"]["text"].replace("\n", " "))
    return "\n".join(out)


def _empty(v) -> bool:
    return v is None or str(v).strip() == ""


# 코드 사전이 Material 칸을 채울 때 쓰는 자(gerp_xlsx.hynix_material 과 같은 모양). gerp_xlsx 는 무거워 여기서 부르지 않는다
MAT_FIELDS = ("sap_code", "material_code")
PART_FIELDS = ("part_no", "hios_no")
_MAT_LIKE = re.compile(r"^[A-Z][A-Z0-9]{5,11}$")       # gerp_xlsx._PLAIN_CODE. 이 모양이 아니면 Material 로 가지 않는다
_HYNIX_IN_PART = re.compile(r"^[A-Z]\d{7}$")           # gerp_xlsx._HYNIX_STRICT. 부품 번호 칸에서 Material 로 가는 모양


def _mat_like(v) -> bool:
    """자재 코드 칸 값이 G-ERP Material 로 갈 수 있는 모양인가. 아니면 화면에는 빈칸이므로 「채우기」 규칙이 덮어써도 된다.

    DUPONT 는 자기 11자리 번호(00011047587)를 자재 코드 칸에 읽어 Material 이 비고, 사람이 R2117286 을 넣었다(11/12).
    """
    return bool(_MAT_LIKE.match(re.sub(r"\s+", "", str(v if v is not None else "")).upper()))


def apply_post(ex: dict, supplier=None) -> list:
    """판독 결과(ex)의 줄에 코드 사전과 값 바꾸기를 적용한다. 바꾼 자리를 돌려준다(줄 번호는 인보이스 줄 순서)."""
    trace = []
    codes = active("code", supplier)
    aliases = active("alias", supplier)
    if not codes and not aliases:
        return trace
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    pl = [x for x in (ex.get("pl_lines") or []) if isinstance(x, dict)]
    for i, line in enumerate(ci):
        desc, part = norm_desc(line.get("description")), norm(line.get("part_no") or line.get("hios_no"))
        done = set()
        for r in codes:
            d = r["data"]
            key = norm_desc(d["key"]) if d["on"] == "desc" else norm(d["key"])
            have = desc if d["on"] == "desc" else part
            if not key or not have:
                continue
            hit = (key in have) if d.get("match") == "contains" else (have == key)
            f = d["field"]
            if not hit or f in done:
                continue
            done.add(f)
            before = line.get(f)
            if not (d.get("mode") == "replace" or _empty(before) or (f in MAT_FIELDS and not _mat_like(before))):
                continue
            line.setdefault("_rules", {})[f] = r["id"]    # 사람이 정한 값. G-ERP Material 은 형식을 따지지 않고 이 값을 쓴다
            if norm(before) == norm(d["value"]):
                continue
            if f in PART_FIELDS and _HYNIX_IN_PART.match(norm(before)) and all(_empty(line.get(k)) for k in MAT_FIELDS):
                line["sap_code"] = str(before).strip()    # 부품 번호 칸에 읽힌 하이닉스 번호는 Material 로 가던 값이다. 바꿔도 잃지 않게 옮긴다
            line[f] = d["value"]
            trace.append({"rule": r["id"], "kind": "code", "line": i, "field": f,
                          "before": "" if before is None else str(before), "after": d["value"]})
    for src, lines in (("ci", ci), ("pl", pl)):
        for i, line in enumerate(lines):
            for f in ALIAS_FIELDS:
                v = line.get(f)
                if _empty(v):
                    continue
                for r in aliases:
                    d = r["data"]
                    if d["field"] == f and norm(v) == norm(d["from"]):
                        line[f] = d["to"]
                        if src == "ci":
                            line.setdefault("_rules", {})[f] = r["id"]
                            trace.append({"rule": r["id"], "kind": "alias", "line": i, "field": f,
                                          "before": str(v), "after": d["to"]})
                        break
    return trace


def check_policy(supplier=None) -> dict:
    """빠짐 검사 네 가지의 켜짐과 금액 허용 차이. 공급사 규칙이 공통 규칙을 이긴다."""
    pol = {c: {"enabled": True, "rule": None} for c in CHECKS}
    pol["amount"]["tolerance"] = 0.01
    for scope in (COMMON, supplier):
        if not scope:
            continue
        for r in all_rules():
            if r["kind"] == "check" and r["status"] == "active" and r["scope"] == scope:
                d = r["data"]
                pol[d["check"]].update(enabled=d.get("enabled", True), rule=r["id"])
                if d["check"] == "amount" and d.get("tolerance") is not None:
                    pol["amount"]["tolerance"] = float(d["tolerance"])
    return pol


def required_columns(supplier=None) -> list:
    """비면 주의로 볼 열 묶음. 묶음 안의 열은 하나만 차도 된다. 공급사가 같은 묶음을 끄면 빠진다."""
    out = {}
    for scope in (COMMON, supplier):
        if not scope:
            continue
        for r in all_rules():
            if r["kind"] == "required" and r["status"] == "active" and r["scope"] == scope:
                key = tuple(r["data"]["columns"])
                if r["data"].get("required", True):
                    out[key] = r["id"]
                else:
                    out.pop(key, None)
    return [list(k) for k in out]


# ───────────────────────────────────────── 내보내기, 가져오기
def export() -> dict:
    with _lock:
        st = _load_raw()
        return {"version": 1, "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                "rules": [r for r in st["rules"].values() if r.get("status") != "deleted"],
                "suppliers": st["suppliers"]}


def import_(data: dict, by="", reason="") -> dict:
    """다른 서버에서 내보낸 규칙을 합친다. id 가 같으면 가져온 쪽이 이긴다."""
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("규칙 파일 꼴이 아닙니다.")
    n = 0
    with _lock:
        st = _load_raw()
        for r in data["rules"]:
            if isinstance(r, dict) and r.get("id") and r.get("kind") in KINDS:
                st["rules"][r["id"]] = r
                n += 1
        st["suppliers"].update(data.get("suppliers") or {})
        _save(st)
    _history("import", by=by, reason=reason, extra={"text": f"규칙 {n}개를 가져왔습니다"})
    return {"rules": n, "suppliers": len(data.get("suppliers") or {})}


def counts() -> dict:
    rs = all_rules()
    out = {"active": 0, "candidate": 0, "off": 0, "by_kind": {k: 0 for k in KINDS}}
    for r in rs:
        if r["status"] in out:
            out[r["status"]] += 1
        if r["status"] == "active":
            out["by_kind"][r["kind"]] += 1
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="우시 판독 규칙. 운영 값을 기본값 파일로 올리거나 파일에서 가져온다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="쓰이는 규칙을 보여 준다")
    e = sub.add_parser("export", help="운영 값을 JSON 으로 낸다")
    e.add_argument("--to-default", action="store_true", help="schema/rules_default.json 에 써서 Git 으로 배포한다")
    i = sub.add_parser("import", help="JSON 파일의 규칙을 운영 값에 합친다")
    i.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for r in all_rules():
            print(f"{STATUS.get(r['status'], r['status']):3} {KINDS[r['kind']]:5} {supplier_name(r['scope']):12} {describe(r)}")
    elif a.cmd == "export":
        data = export()
        if a.to_default:
            for r in data["rules"]:
                r.pop("evidence", None)
            with open(DEFAULT_FILE, "w", encoding="utf-8") as fh:
                json.dump({"rules": data["rules"], "suppliers": data["suppliers"]}, fh, ensure_ascii=False, indent=1)
            print(f"썼습니다: {DEFAULT_FILE}")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=1))
    elif a.cmd == "import":
        with open(a.file, encoding="utf-8") as fh:
            print(import_(json.load(fh), by="명령줄"))


if __name__ == "__main__":
    main()
