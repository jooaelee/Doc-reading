# -*- coding: utf-8 -*-
"""코드 찾기에 쓸 마스터 파일을 만든다.

거래처 마스터는 KEROL 의 Company 화면과 Customer Company 화면에서 받아야 한다. 아직 없어서
`schema/company-master-예시.csv` 로 어떤 모양이 필요한지만 적어 둔다.

장소 마스터는 저장소에 있는 KEROL 배터리 데이터로 실제 짝을 만들 수 있다. 고객 포털 내보내기에는
`BUSAN, KOREA` 처럼 이름이 있고 데이터 분석 내보내기에는 `KRPUS` 처럼 코드가 있으며, 둘은 BL 번호로
이어진다. 이 짝을 세어 `schema/locations.json` 으로 적는다.

  python3 tools/kerol_master.py --locations       # locations.json 다시 만들기
  python3 tools/kerol_master.py --check           # 저장된 것과 같은지만 본다
  python3 tools/kerol_master.py --company-template # 거래처 마스터 예시 파일 만들기
"""
import argparse, collections, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import wuxi_paths  # noqa: E402
DB = os.path.join(wuxi_paths.DATA, "kerol.sqlite")
LOC = os.path.join(wuxi_paths.SCHEMA, "locations.json")
COMPANY_TEMPLATE = os.path.join(wuxi_paths.SCHEMA, "company-master-예시.csv")

# 항공 건에 나온 출발지와 도착지. KEROL 의 Global Location 을 받기 전까지는 임시다.
PROVISIONAL_AIR = [
    ("SINGAPORE", "SGSIN"), ("TAIPEI", "TWTPE"), ("DALIAN", "CNDLC"), ("INCHEON", "KRICN"),
    ("SEOUL", "KRSEL"), ("PUDONG", "CNPVG"), ("SHANGHAI", "CNSHA"), ("HONG KONG", "HKHKG"),
    ("CHONGQING", "CNCKG"), ("WUXI", "CNWUX"), ("NARITA", "JPNRT"), ("TOKYO", "JPTYO"),
    ("HANOI", "VNHAN"), ("HO CHI MINH", "VNSGN"), ("PENANG", "MYPEN"), ("BANGKOK", "THBKK"),
]


def build_locations(db=DB):
    con = sqlite3.connect(db)
    pairs = collections.defaultdict(collections.Counter)
    for name_col, code_col in (("origin", "pol_code"), ("dest", "pod_code")):
        q = (f"select p.{name_col}, a.{code_col} from portal_grid p join data_analysis a on p.bl_no = a.bl_no "
             f"where p.{name_col} is not null and p.{name_col} != '' "
             f"and a.{code_col} is not null and a.{code_col} != ''")
        for nm, cd in con.execute(q):
            pairs[nm.strip()][cd.strip()] += 1
    con.close()
    confirmed, ambiguous = [], []
    for nm, c in sorted(pairs.items()):
        (ambiguous if len(c) > 1 else confirmed).append(
            {"code": c.most_common(1)[0][0], "name": nm, "address": "", "건수": sum(c.values()),
             **({"다른 코드": dict(c)} if len(c) > 1 else {})})
    return {
        "version": "0.1",
        "updated": "2026-09-10",
        "무엇인가": "장소 이름을 KEROL 코드로 바꾸는 마스터. 서류에는 이름이 적혀 있고 KEROL 은 코드를 받는다.",
        "만드는 법": "python3 tools/kerol_master.py --locations",
        "확인된 것의 출처": ("저장소의 KEROL 배터리 내보내기 두 벌. 고객 포털에는 이름이 있고 데이터 분석에는 "
                      "코드가 있으며 BL 번호로 이어진다. 실제로 같이 쓰인 짝만 남겼다."),
        "임시의 출처": "항공 건에 나온 출발지와 도착지. 표준 코드로 적었지만 KEROL 이 쓰는 값인지 확인하지 않았다.",
        "다음": "KEROL 의 Global Location(CM_CITYCODE_M001) 을 받으면 통째로 바꾼다.",
        "확인된 것": confirmed,
        "이름 하나에 코드 여럿": ambiguous,
        "임시": [{"code": cd, "name": nm, "address": "", "확인": False} for nm, cd in PROVISIONAL_AIR],
    }


def load_locations(path=LOC, provisional=False):
    """codefind.CodeIndex 에 넣을 목록. 기본은 확인된 것만 쓴다."""
    with open(path, encoding="utf-8") as f: d = json.load(f)
    out = [dict(e) for e in d["확인된 것"]]
    if provisional: out += [dict(e) for e in d["임시"]]
    return out


TEMPLATE = """code,name,address,linked_code,kind
# KEROL 거래처 마스터. Company(CM_CUST_M001) 와 Customer Company(CM_CUST_COMPANY_M001) 화면에서 받는다.
# code        KEROL 코드. 보기: NDCC001
# name        거래처 이름. 서류에 적히는 영문 이름과 같은 자리
# address     주소 한 줄. 이름이 같은 거래처를 가를 때 쓴다
# linked_code Linked Company 로 이어진 반대쪽 코드. Company 면 Customer Company 코드, 그 반대도 같다
# kind        company 또는 customer_company
NDCC001,NDEC CO. LTD.,"88 SEONGSAN-RO, CHANGWON, KOREA",CNDEC01,company
NDCC002,NDEC CO. LTD.,"120 GASAN DIGITAL 1-RO, SEOUL, KOREA",CNDEC02,company
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locations", action="store_true", help="locations.json 다시 만들기")
    ap.add_argument("--check", action="store_true", help="저장된 것과 같은지만 본다")
    ap.add_argument("--company-template", action="store_true", help="거래처 마스터 예시 파일 만들기")
    a = ap.parse_args()
    if a.company_template:
        with open(COMPANY_TEMPLATE, "w", encoding="utf-8") as f: f.write(TEMPLATE)
        print(os.path.relpath(COMPANY_TEMPLATE, ROOT))
        return 0
    data = build_locations()
    new = json.dumps(data, ensure_ascii=False, indent=1)
    if a.check:
        old = ""
        if os.path.exists(LOC):
            with open(LOC, encoding="utf-8") as f: old = f.read()
        if old.strip() == new.strip():
            print(f"같습니다. 확인된 것 {len(data['확인된 것'])}개")
            return 0
        print("다릅니다. python3 tools/kerol_master.py --locations 로 다시 만드십시오.")
        return 1
    os.makedirs(os.path.dirname(LOC), exist_ok=True)
    with open(LOC, "w", encoding="utf-8") as f: f.write(new + "\n")
    print(f"{os.path.relpath(LOC, ROOT)} 확인된 것 {len(data['확인된 것'])}개, "
          f"이름 하나에 코드 여럿 {len(data['이름 하나에 코드 여럿'])}개, 임시 {len(data['임시'])}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
