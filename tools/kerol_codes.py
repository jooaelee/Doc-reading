# -*- coding: utf-8 -*-
"""KEROL 의 닫힌 코드 목록을 한 파일로 모은다.

`data/kerol_reference_data.json` 은 2026년 7월 28일에 KEROL 실계정에서
받은 응답을 그대로 담고 있다. 화면마다 모양이 달라서 검증기가 바로 쓰기 어렵다. 이 도구가
`schema/codes.json` 으로 펴서 적는다.

  python3 tools/kerol_codes.py                 # codes.json 다시 만들기
  python3 tools/kerol_codes.py --check         # 다시 만든 것과 저장된 것이 같은지만 본다
  python3 tools/kerol_codes.py --compare SO_UPLOAD_양식.xlsx   # 양식의 Code 시트와 견준다

검증기(`tools/docpipe.py`)는 `load(path)` 로 읽는다. 양식의 Code 시트 대신 이것을 쓰면
KEROL 이 실제로 받는 값으로 검증한다.
"""
import json, os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import wuxi_paths  # noqa: E402
RAW = os.path.join(wuxi_paths.DATA, "kerol_reference_data.json")
OUT = os.path.join(wuxi_paths.SCHEMA, "codes.json")

# 양식과 파이프라인이 부르는 이름 → 이 파일의 그룹 이름
ALIASES = {
    "blType": "BL_TYPE", "incotermsCode": "INCO_TYPE", "cargoType": "CARGO_TYPE",
    "packageUnit": "ITEM_UNIT_CD", "serviceTermCode": "SVC_TERM", "serviceCode": "SERVICE_CD",
    "uldCode": "ULD_CNTR", "transportType": "TRANSPORT_METHOD", "manifestType": "MANIFEST_CARGO_TYPE",
    "Payable Term": "PAYABLE_TERM", "Kind of Haulage": "HAULAGE_TYPE", "WeightUnit": "WEIGHT_UNIT",
    "Forwarding Type": "OD_ORD_TYPE", "Order Type": "ORDER_TYPE", "service group cd": "SERVICE_GRP",
}
WEIGHT_UNITS = ["KG", "TON", "G", "LB"]


def _read(p):
    if not os.path.exists(p): return ""
    with open(p, encoding="utf-8") as f: return f.read()


def build(raw_path=RAW):
    with open(raw_path, encoding="utf-8") as f: d = json.load(f)
    groups = {}
    for x in d["getMultiCodeList.json"]["codeList"]:
        groups.setdefault(x["CODE_GROUP_CD"], {})[x["CODE_CD"]] = x["CODE_NM"]

    uld = d["uldCode/selectUldCodeList.json"]["uldCodeList"]
    groups["ULD_CNTR"] = {x["ULD_CD"]: x["ULD_NM"] for x in uld if x["ULD_TYPE"] == "CNTR"}
    groups["ULD_TRUK"] = {x["ULD_CD"]: x["ULD_NM"] for x in uld if x["ULD_TYPE"] == "TRUK"}

    svc = d["getServiceCodeList.json"]["codeList"]
    groups["SERVICE_CD"] = {x["SERVICE_CD"]: x["SERVICE_NM"] for x in svc}
    groups["SERVICE_GRP"] = {g: g for g in sorted({x["SERVICE_GRP_CD"] for x in svc})}

    ot = d["Notification/subOrderTypeList.json"]["orderTypeList"]
    groups["ORDER_TYPE"] = {x["CODE_CD"]: x["CODE_NM"] for x in ot}
    groups["BRANCH"] = {x["BRANCH_CD"]: x["ORG_NM"] for x in d["getBranchList.json"]["branchs"]}
    groups["WEIGHT_UNIT"] = {u: groups["ITEM_UNIT_CD"][u] for u in WEIGHT_UNITS if u in groups.get("ITEM_UNIT_CD", {})}

    per_customer = sorted({x["ACT_CUSTOMER_CD"] for x in ot})
    return {
        "version": "0.1",
        "받은 날": "2026-07-28",
        "무엇인가": "KEROL 이 실제로 받는 닫힌 코드 목록. 검증기가 이 목록으로 값을 본다.",
        "만드는 법": "python3 tools/kerol_codes.py",
        "원본": "data/kerol_reference_data.json (KEROL 실계정 응답)",
        "주의": [
            "ORDER_TYPE 은 고객과 법인마다 쓸 수 있는 값이 다르다. 여기 적은 다섯은 코드 자체의 목록이고, "
            f"실제 조합은 고객 {len(per_customer)}개에 걸쳐 {len(ot)}줄이다.",
            "ULD_CNTR 은 컨테이너 규격이고 ULD_TRUK 은 차량 규격이다. 양식의 uldCode 는 ULD_CNTR 쪽이다.",
            "WEIGHT_UNIT 은 ITEM_UNIT_CD 에서 무게 단위만 골라낸 것이다. KEROL 에 따로 있는 목록이 아니다.",
        ],
        "aliases": ALIASES,
        "groups": {k: dict(sorted(v.items())) for k, v in sorted(groups.items())},
    }


def load(path=OUT):
    """검증기가 쓰는 꼴. {부르는 이름: {코드}} 로 편다. 그룹 이름과 별칭 둘 다 받는다."""
    with open(path, encoding="utf-8") as f: d = json.load(f)
    out = {g: set(v) for g, v in d["groups"].items()}
    for alias, group in d.get("aliases", {}).items():
        if group in out:
            out[alias] = out[group]
    return out


def compare_form(xlsx, data=None):
    """SO 업로드 양식의 Code 시트와 KEROL 목록의 차이. (이름, 양식만, KEROL만) 목록을 낸다."""
    import openpyxl
    data = data or build()
    ws = openpyxl.load_workbook(xlsx, data_only=True)["Code"]
    form, cur = {}, None
    for r in ws.iter_rows(min_row=2, values_only=True):
        name = r[1] if len(r) > 1 else None
        code = r[2] if len(r) > 2 else None
        if name: cur = str(name).strip()
        if cur and code is not None: form.setdefault(cur, set()).add(str(code).strip())
    form.pop("ELEMENT NAME", None)
    rows = []
    for elem, codes in sorted(form.items()):
        group = ALIASES.get(elem)
        kset = set(data["groups"].get(group, {})) if group else set()
        rows.append((elem, group, sorted(codes - kset), sorted(kset - codes), len(codes), len(kset)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="저장된 codes.json 과 같은지만 본다")
    ap.add_argument("--compare", help="SO 업로드 양식 xlsx 와 견준다")
    a = ap.parse_args()
    data = build()
    if a.compare:
        for elem, group, only_form, only_kerol, nf, nk in compare_form(a.compare, data):
            mark = "같다" if not only_form and not only_kerol else "다르다"
            print(f"{elem:18} {group or '-':20} 양식 {nf:3} KEROL {nk:3}  {mark}")
            if only_form: print(f"    양식에만: {', '.join(only_form)}")
            if only_kerol: print(f"    KEROL에만: {', '.join(only_kerol[:12])}{' ...' if len(only_kerol) > 12 else ''}")
        return 0
    new = json.dumps(data, ensure_ascii=False, indent=1)
    if a.check:
        old = _read(OUT)
        if old.strip() == new.strip():
            print(f"같습니다. 그룹 {len(data['groups'])}개")
            return 0
        print("다릅니다. python3 tools/kerol_codes.py 로 다시 만드십시오.")
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f: f.write(new + "\n")
    n = sum(len(v) for v in data["groups"].values())
    print(f"{os.path.relpath(OUT, ROOT)} 그룹 {len(data['groups'])}개, 코드 {n}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
