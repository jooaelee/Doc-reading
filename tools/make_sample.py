"""지어낸 우시 서류 묶음(인보이스, 포장명세서, 통관 용도설명서)과 정답표를 만든다.

고객 서류 없이 설치가 끝났는지, 품목 줄을 하나도 빠짐없이 읽는지 확인하는 데 쓴다. 회사, 번호, 품목은 모두 가짜다.

  python tools/make_sample.py                                        # 3줄짜리 작은 묶음(work/samples/sample)
  python tools/make_sample.py --lines 120 --invoices 3 --name long   # 여러 쪽, 여러 장(빠짐 시험)
  python tools/selfcheck.py [--name long] [--scan]                   # 실제로 읽고 정답과 견준다

text 는 글자층이 있는 PDF, scan 은 같은 서류를 그림으로만 찍은 PDF(OCR 길을 탄다)다.
쪽마다 줄이 넘치면 다음 쪽으로 넘어가고 표 머리를 다시 찍는다. 실제 서류처럼 긴 품명, 같은 품명 다른 사양, 같은 코드
여러 줄(수리품 되풀이)을 섞는다.
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402

WORDS = ["WAFER CARRIER", "O-RING VITON", "QUARTZ TUBE", "GATE VALVE", "ESC CHUCK", "FOCUS RING", "SHOWER HEAD",
         "ROBOT BLADE", "BELLOWS", "HEATER JACKET", "PUMP SEAL KIT", "MFC UNIT", "CERAMIC PIN", "EDGE RING",
         "TURBO PUMP CONTROLLER", "RF MATCHER BOARD", "SLIT VALVE DOOR", "LIFT PIN ASSY"]
SPEC = ["25 SLOT", "3.5MM", "300MM", "DN40", "TYPE A", "TYPE B", "REV.2", "FOR 12INCH", "SET OF 4", "SPARE", "REPAIR"]
UNITS = ["EA", "PCS", "SET", "EA", "EA"]
ORIGINS = ["KOREA", "JAPAN", "USA", "GERMANY", "CHINA"]
CN = ["晶圆载具", "密封圈", "石英管", "闸阀", "静电卡盘", "聚焦环", "喷淋头", "机械手叶片", "波纹管", "加热套"]
PER_PAGE = 34


def build_lines(n_lines, n_inv, seed=7):
    rnd = random.Random(seed)
    invs = []
    per = [n_lines // n_inv + (1 if i < n_lines % n_inv else 0) for i in range(n_inv)]
    serial = 0
    for k in range(n_inv):
        inv_no = f"SPL-INV-2610-{k + 1:04d}"
        po = f"45000{12345 + k * 11:05d}"
        rows = []
        for i in range(per[k]):
            serial += 1
            if rows and rnd.random() < .12:       # 수리품: 앞 줄과 같은 코드, 같은 품명이 되풀이된다
                desc, sap = rows[-1]["description"], rows[-1]["sap_code"]
            else:
                desc = f"{rnd.choice(WORDS)} {rnd.choice(SPEC)}"
                if rnd.random() < .25:
                    tail = f" P/N {rnd.randint(100, 999)}-{rnd.randint(1000, 9999)}"
                    # 인보이스 품명 칸 폭(52자)을 넘으면 붙이지 않는다. 잘린 품명은 AI 가 다른 줄을 보고
                    # 끝을 채워 읽어(「…WITH」를 「…WITH BRACKET」으로) 읽기 시험이 아니라 자르기 시험이 된다
                    for t in (tail + " WITH BRACKET", tail):
                        if len(desc + t) <= 52:
                            desc += t
                            break
                sap = f"F{rnd.randint(100, 999):03d}{rnd.choice('ABCDEF')}{serial:04d}"
            qty = rnd.choice([1, 2, 4, 6, 10, 25, 40, 100, 500])
            price = round(rnd.uniform(1.5, 4800), 2)
            rows.append({"seq": i + 1, "invoice_no": inv_no, "po_no": po, "po_item_no": f"{(i + 1) * 10:05d}",
                         "sap_code": sap, "description": desc, "qty": qty, "unit": rnd.choice(UNITS),
                         "unit_price": price, "amount": round(qty * price, 2),
                         "country_of_origin": rnd.choice(ORIGINS), "_model": f"M-{serial:04d}",
                         "_cn": rnd.choice(CN), "_net": round(rnd.uniform(.2, 80), 1)})
        invs.append({"invoice_no": inv_no, "po_no": po, "currency": "USD",
                     "total": round(sum(r["amount"] for r in rows), 2), "lines": rows})
    return invs


def _pages(doc, title, head, colhead, rows, foot):
    """줄이 넘치면 다음 쪽에 표 머리를 다시 찍는다."""
    for start in range(0, max(len(rows), 1), PER_PAGE):
        chunk = rows[start:start + PER_PAGE]
        page = doc.new_page(width=842, height=595)              # 가로 A4, 넓은 표
        y = 36
        page.insert_text((36, y), title, fontsize=14, fontname="helv")
        page.insert_text((760, y), f"PAGE {start // PER_PAGE + 1}", fontsize=8, fontname="helv")
        y += 20
        for h in head:
            page.insert_text((36, y), h, fontsize=8, fontname="helv")
            y += 11
        y += 4
        page.insert_text((36, y), colhead, fontsize=7, fontname="cour")
        y += 12
        for ln in chunk:
            text, cjk = ln if isinstance(ln, tuple) else (ln, None)
            page.insert_text((36, y), text, fontsize=7, fontname="cour")
            if cjk:
                page.insert_text((780, y), cjk, fontsize=7, fontname="china-s")
            y += 12
        if start + PER_PAGE >= len(rows):
            y += 6
            for f in foot:
                page.insert_text((36, y), f, fontsize=8, fontname="helv")
                y += 11


def build(out_dir, n_lines=3, n_inv=1, name="sample"):
    import pymupdf
    invs = build_lines(n_lines, n_inv)
    doc = pymupdf.open()
    for inv in invs:
        head = ["SELLER: SAMPLE PARTS CO., LTD. (FICTIONAL)  123 SAMPLE-RO, SEOUL, KOREA",
                "BUYER: SK HYNIX SEMICONDUCTOR (CHINA) LTD. WUXI",
                f"INVOICE NO: {inv['invoice_no']}    DATE: 2026-10-20    TERMS: FCA INCHEON    CURRENCY: USD"]
        rows = [f"{r['seq']:<4}{r['po_no']:<12}{r['po_item_no']:<7}{r['sap_code']:<10}{r['description'][:52]:<53}"
                f"{r['qty']:>5} {r['unit']:<4}{r['unit_price']:>10,.2f}{r['amount']:>12,.2f} {r['country_of_origin']}"
                for r in inv["lines"]]
        _pages(doc, "COMMERCIAL INVOICE", head,
               "SEQ PO NO.      ITEM   SAP CODE  DESCRIPTION                                            QTY UNIT UNIT PRICE      AMOUNT ORIGIN",
               rows, [f"TOTAL AMOUNT: USD {inv['total']:,.2f}", f"TOTAL LINES: {len(rows)}"])
    for inv in invs:
        head = [f"INVOICE NO: {inv['invoice_no']}    BUYER: SK HYNIX SEMICONDUCTOR (CHINA) LTD. WUXI"]
        rows = [f"{r['seq']:<4}{r['po_no']:<12}{r['po_item_no']:<7}{r['description'][:52]:<53}{r['qty']:>5} {r['unit']:<4}"
                f"{r['_net']:>8.1f}{r['_net'] * 1.2:>9.1f}  C/NO.{(r['seq'] - 1) // 5 + 1}" for r in inv["lines"]]
        _pages(doc, "PACKING LIST", head,
               "SEQ PO NO.      ITEM   DESCRIPTION                                            QTY UNIT  NET(KG) GROSS(KG)  CASE",
               rows, [f"TOTAL: {len(rows)} LINES"])
    cn_rows = []
    for inv in invs:
        for r in inv["lines"]:
            cn_rows.append((f"{len(cn_rows) + 1:<4}{r['sap_code']:<10}{r['_model']:<8}{r['description'][:44]:<45}SAMPLE SAMPLE "
                            f"PRODUCTION NEW {r['country_of_origin']:<8}", r["_cn"]))
    _pages(doc, "USAGE DECLARATION", ["CHINA CUSTOMS CLEARANCE - IMPORT GOODS INFORMATION",
                                      "INVOICE NO: " + ", ".join(i["invoice_no"] for i in invs)],
           "NO  SKhynix Systemic Code / Model/Specification / English Name / Brand / Maker / Use For / "
           "New,Old or Repair / Country of Origin / Chinese Name", cn_rows, ["Goods Photo: (attached)"])
    for sub in ("text", "scan"):
        os.makedirs(os.path.join(out_dir, name, sub), exist_ok=True)
    doc.save(os.path.join(out_dir, name, "text", f"{name}_bundle.pdf"))
    scan = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        sp = scan.new_page(width=page.rect.width, height=page.rect.height)
        sp.insert_image(sp.rect, pixmap=pix)
    scan.save(os.path.join(out_dir, name, "scan", f"{name}_bundle_scan.pdf"))
    ans = {"invoices": [{k: v for k, v in i.items() if k != "lines"} for i in invs],
           "lines": [{k: v for k, v in r.items() if not k.startswith("_")} for i in invs for r in i["lines"]],
           "pages": doc.page_count}
    with open(os.path.join(out_dir, name, "answer.json"), "w", encoding="utf-8") as f:
        json.dump(ans, f, ensure_ascii=False, indent=1)
    return os.path.join(out_dir, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=wuxi_paths.work("samples"))
    ap.add_argument("--lines", type=int, default=3)
    ap.add_argument("--invoices", type=int, default=1)
    ap.add_argument("--name", default="sample")
    a = ap.parse_args()
    print(build(a.out, a.lines, a.invoices, a.name))
