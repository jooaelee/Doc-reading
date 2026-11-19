"""남는 줄 정리 잠금: 못 이은 포장명세서 줄(gerp_xlsx.fold_pl_rows)과 수리 인보이스의 쌍 줄(docpipe.price_twins).

정답 46건에서 사람이 지운 82줄(포장명세서 55, 인보이스 27)을 줄이려고 더했다(11/12). 사례는 실제 서류에서 땄다.
AI 를 부르지 않는다.
"""
import gerp_xlsx as G
import docpipe as D

DESC4 = "空白线路板/四层/化金 printed circuit board/four layers/ENIG"


def ci(q, inv="INV1", unit="PCS", desc=DESC4, **codes):
    return {"출처": "CI", "Invoice No.": inv, "B/L quantity": q, "단위": unit, "품명": desc, "Case No.": "",
            "순중량(kg)": "", "총중량(kg)": "", **codes}


def pl(q, unit="PCS", desc="", case="", **codes):
    return {"출처": "PL", "Invoice No.": "", "B/L quantity": q, "단위": unit, "품명": desc, "Case No.": case, **codes}


def kinds(rows):
    return [r["출처"] for r in rows]


def test_상자별로_나뉜_줄은_같은_품목의_수량_합이_맞으면_뺀다():
    # PLISZ2H00667: PL 품명 안에 인보이스 코드가 붙어 있고, 상자 수량 합이 인보이스 수량과 같다
    rows = [ci(311, **{"Vendor Part No.": "EAX01957931/1015734"}), ci(355, **{"Vendor Part No.": "EAX01927933/1015585"}),
            pl(300, desc=DESC4 + " EAX01957931 1015734", case="1~6"), pl(11, desc=DESC4 + " EAX01957931 1015734", case="7~7"),
            pl(250, desc=DESC4 + " EAX01927933 1015585", case="8~12"), pl(105, desc=DESC4 + " EAX01927933 1015585", case="13~17")]
    st = G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"] and st["same_item"] == 4
    assert rows[0]["Case No."] == "1~6, 7~7" and rows[1]["Case No."] == "8~12, 13~17"


def test_코드_표기가_달라도_같은_코드다():
    # PLISZ2H00486 은 띄어쓰기와 밑줄, PLISZ2H00821 은 PL 이 코드 뒤에 내부 번호를 붙임
    rows = [ci(308, **{"Part No.": "LX-SMA078X232-03 260203"}), pl(240, **{"Part No.": "LX-SMA078X232-03_260203"}),
            pl(68, **{"Part No.": "LX-SMA078X232-03_260203"})]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI"] and rows[0]["Case No."] == ""     # 첫 PL 은 이음(joined)
    rows = [ci(101, **{"Part No.": "EAX01927933"}), ci(141, **{"Part No.": "EAX01927933"}),
            pl(200, **{"Part No.": "EAX01927933/1015585"}), pl(42, **{"Part No.": "EAX01927933/1015585"})]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"]


def test_수량이_넘치면_그대로_둔다():
    # PLISZ2H00644: 인보이스 215 를 1 로 읽음. PL 수량이 넘치므로 사람이 보게 남긴다
    rows = [ci(1, **{"Part No.": "EAX01957931"}), pl(15, desc=DESC4), pl(22, desc=DESC4)]
    rows[0]["Case No."] = "1~3"
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "PL", "PL"]


def test_인보이스에_없는_품목은_그대로_둔다():
    rows = [ci(10, **{"Part No.": "AAA111"}), pl(5, **{"Part No.": "ZZZ999"}), pl(3, desc="PALLET")]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "PL", "PL"]


def test_품명도_코드도_없는_상자_줄은_남은_수량_합이_꼭_맞을_때만_뺀다():
    # PLISZ2H00693: 이은 두 줄(29, 20)을 뺀 인보이스 수량 716 과 PL 상자 줄 합 716
    rows = [ci(177), ci(134), ci(29), ci(405), ci(20)]
    rows[2]["Case No."], rows[4]["Case No."] = "12~12", "10~10"
    boxes = [200, 8, 13, 34, 2, 13, 21, 300, 12, 24, 11, 20, 19, 39]
    G.fold_pl_rows(rows + [pl(q, desc="") for q in boxes])
    full = rows + [pl(q, desc="") for q in boxes]
    assert G.fold_pl_rows(full)["boxes"] == 14 and kinds(full) == ["CI"] * 5
    off = rows + [pl(q, desc="") for q in boxes[:-1]]          # 39 하나가 모자라면 아무것도 빼지 않는다
    G.fold_pl_rows(off)
    assert kinds(off).count("PL") == 13


def test_무게_줄과_수량_없는_사본_줄():
    # PNS26090853: PL 수량이 KG. 인보이스가 여러 장이고 PL 줄에는 인보이스 번호가 없다
    rows = [ci(2, inv="A", unit="EA", desc="200MM  WAFER"), ci(37, inv="B", unit="EA", desc="200MM WAFER"),
            pl(20, unit="KG", desc="200mm WAFER"), pl(17, unit="KG", desc="200mm WAFER")]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"]
    # HDLCA112870: PL 줄에 수량이 없고 인보이스와 다른 칸에서 코드가 겹친다
    rows = [ci(10, desc="ROD", **{"Material": "P3022472", "Part No.": "14312"}), pl("", desc="ROD", **{"Material": "P3022472"})]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI"]


def test_상자_단위_줄은_인보이스에_코드가_없을_때만_뺀다():
    # PLISZ2H01140: 인보이스는 품명뿐, PL 은 부품 번호와 상자 수(CTN)
    rows = [ci(1130, desc="PCB FOUR"), ci(237, desc="PCB SIX"), pl(17, unit="CTN", **{"Part No.": "LX-STA125"}),
            pl(1, unit="CTN", **{"Part No.": "LX-SMA078"})]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"]
    rows = [ci(1130, **{"Part No.": "AAA111"}), pl(17, unit="CTN", **{"Part No.": "LX-STA125"})]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "PL"]


def line(code, amt, qty=1):
    return {"material_code": code, "qty": qty, "amount": amt}


def test_수리_인보이스의_쌍_줄은_총액에_맞는_쪽만_남긴다():
    codes = ["MMF07734", "MMF07461", "P3059904", "MMF07366"]
    repair = [line(c, a) for c, a in zip(codes, [1450.85, 1450.85, 2012.54, 1404.95])]
    ncv = [line(c, a) for c, a in zip(codes, [3500, 3500, 13000, 3500])]
    tot = sum(x["amount"] for x in repair)
    assert D.price_twins(repair + ncv, tot) == [4, 5, 6, 7]
    assert D.price_twins(ncv + repair, tot) == [0, 1, 2, 3]
    mixed = [x for pair in zip(repair, ncv) for x in pair]          # 줄마다 두 값이 번갈아
    assert D.price_twins(mixed, tot) == [1, 3, 5, 7]
    # 판독이 Total Amount 를 받아 꼭 맞지 않아도, 한쪽만 총액을 넘으면 넘는 쪽을 뺀다
    assert D.price_twins(repair + ncv, tot + 5000) == [4, 5, 6, 7]
    # 코드 한 줄 오타(스캔 사본)는 봐준다. 두 줄이면 다른 줄이다
    typo = [dict(x) for x in ncv]
    typo[3]["material_code"] = "MMF07202"
    assert D.price_twins(repair + typo, tot) == [4, 5, 6, 7]
    typo[2]["material_code"] = "P0000000"
    assert D.price_twins(repair + typo, tot) == []


def test_수리_인보이스가_아니면_빼지_않는다():
    a = [line("A1", 10), line("B1", 20)]
    b = [line("C1", 10), line("D1", 20)]
    assert D.price_twins(a + b, 30) == []                 # 다른 품목
    assert D.price_twins(a + a, 60) == []                 # 합이 총액과 맞음
    assert D.price_twins(a + [line("A1", 99), line("B1", None)], 30) == []   # 금액이 빈 줄


def test_하이닉스_판독은_인보이스별_총액으로_쌍_줄을_뺀다():
    ex = {"ci_lines": [dict(line("A1", 10), invoice_no="I1"), dict(line("B1", 20), invoice_no="I1"),
                       dict(line("A1", 70), invoice_no="I1"), dict(line("B1", 80), invoice_no="I1"),
                       dict(line("Z9", 5), invoice_no="I2")],
          "invoice_totals": [{"invoice_no": "I1", "total": 30, "currency": "USD"},
                             {"invoice_no": "I2", "total": 5, "currency": "USD"}]}
    D.set_prompt("h1")              # 하이닉스 판. 코드 칸을 글자로 둔다
    try:
        out = D.postprocess(ex)
    finally:
        D.set_prompt("v3")
    assert [(l["material_code"], l["amount"]) for l in out["ci_lines"]] == [("A1", 10), ("B1", 20), ("Z9", 5)]
    assert out["ci_lines_dropped"] == 2


def test_인보이스에만_코드가_있어도_품명이_같으면_같은_품목이다():
    # HDLCA124559 VERSUM: 인보이스는 Material 과 SET, PL 은 코드 없이 같은 품명과 ea. 상자 둘로 나뉜 줄도 있다
    rows = [ci(1, unit="SET", desc="Sub_C2F_Cooling Cabinet_5%B2H6/N2", Material="EBE01026"),
            ci(1, unit="SET", desc="Sub_C2F_C-Bundle_HCl", Material="EBE07608"),
            pl(1, unit="ea", desc="Sub_C2F_Cooling Cabinet_5%B2H6/N2"), pl(1, unit="ea", desc="Sub_C2F_C-Bundle_HCl"),
            pl(1, unit="ea", desc="Sub_C2F_C-Bundle_HCl")]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"]
    # FLU10638 DUPONT CMP: 인보이스는 KG 와 ™ 표시, PL 은 드럼 수(DR)
    d = "Acuplane™ LK393C4-CON Slurry DRUM"
    rows = [ci(1428, unit="KG", desc=d, **{"Vendor Part No.": "11144174"}), ci(476, unit="KG", desc=d, Material="R3046328"),
            pl(6, unit="DR", desc=d.replace("™", "")), pl(2, unit="DR", desc=d.replace("™", ""))]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "CI"]
    # PL 줄에 코드가 있는데 안 겹치면 품명이 같아도 다른 품목이다
    rows = [ci(5, desc="VALVE", Material="AAA111"), pl(5, desc="VALVE", Material="ZZZ999")]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "PL"]


def test_품명에_든_코드나_LOT_번호만_붙은_품명도_같은_품목이다():
    # SFSA2610028: 인보이스 품명에 PL 코드 12097013 이 들어 있고, PL 은 상자 넷으로 나뉜다(4+41+1+14=60)
    d = 'IKONIC(TM) 4140H WINDOW PAD FD1 30" ACAQ;5C05'
    rows = [ci(60, unit="EA", desc="00012097013 X " + d, Material="P3252596")] + \
        [pl(q, unit="EA", desc=d, **{"Part No.": "12097013"}) for q in (4, 41, 1, 14)]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI"]
    # NEC49849015 TOPCO: PL 품명이 인보이스 품명의 뒷부분이고 Lot 번호가 붙는다(32+8+4+4=48)
    rows = [ci(48, unit="GAL", desc="PHOTO RESIST NP _SOC_SEFS-1842_2.60CP_3.785L_", Material="R3082115")] + \
        [pl(q, unit="GALS", desc="NP _SOC_SEFS-1842_2.60CP_3.785L_ Lot No.260612D1") for q in (32, 8, 4, 4)]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI"]
    # 짧은 낱말이 우연히 들어 있는 것은 같은 품목이 아니다
    rows = [ci(5, desc="O-RING VALVE BODY ASSY", Material="AAA111"), pl(5, desc="VALVE")]
    G.fold_pl_rows(rows)
    assert kinds(rows) == ["CI", "PL"]
