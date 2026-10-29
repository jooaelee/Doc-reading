# -*- coding: utf-8 -*-
"""같은 서류를 프롬프트 두 판으로 돌려 견준다.

칸을 늘리면 기존 칸이 흔들리는지는 재 보기 전에 모른다. v4 는 v3 보다 열 칸을 더
뽑는데, 그 대가로 v3 이 잘 맞히던 칸이 떨어지면 두 번 호출로 나눠야 한다. 이 도구는
그 하나만 답한다. **두 판이 공통으로 뽑는 칸에서 값이 갈리는가.**

    python3 tools/compare_prompts.py --root work/docs_raw --n 5
    python3 tools/compare_prompts.py --root work/docs_raw --n 5 --a v3 --b v4

ANTHROPIC_API_KEY 가 있어야 한다. 케이스마다 두 번 부르므로 --n 은 작게 시작한다.
서류와 결과는 저장소에 넣지 않는다. --save 로 낸 파일도 마찬가지다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다. 지금은 %d.%d 입니다."
             % sys.version_info[:2])

import argparse, json, os, random, re, shutil, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe


def cases(root, n, seed=0, only_scan=False):
    """케이스 폴더에서 PDF 를 모아 꾸러미로 만든다."""
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        pdfs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".pdf"))
        if pdfs:
            out.append((name, pdfs))
    if only_scan:
        out = [(nm, ps) for nm, ps in out if _is_scan(ps)]
    random.Random(seed).shuffle(out)
    return out[:n]


def _is_scan(paths):
    """글자층이 없는 서류만 고른다. 스캔본에서 값이 어떻게 갈리는지 따로 보고 싶을 때 쓴다."""
    for p in paths:
        for page in docpipe.load_pdf(p):
            if page.text.strip():
                return False
    return True


# 장소 칸은 뒤에 붙는 나라 이름을 뗀다. 같은 회차 안에서도 INCHEON 과 INCHEON, KOREA 가 섞여 나온다.
PLACE_KEYS = {"origin", "destination"}


def flat(v, key=None):
    """견주기 좋게 편다. 배열은 JSON 문자열 하나로 눌러 본다.

    key 를 주면 그 칸에만 맞는 꼴 맞추기를 더 한다. 지금은 장소 칸의 나라 이름뿐이다.
    """
    if key in PLACE_KEYS and isinstance(v, str):
        v = v.split(",")[0]
    if key and key.endswith("_address") and isinstance(v, str):
        # 주소 뒤의 전화와 담당자는 회차마다 붙었다 떨어졌다 한다. 코드 찾기는 앞부분만 쓴다.
        v = re.split(r"\b(?:TEL|FAX|ATTN|MOBILE|E-?MAIL)\b", v, 1, flags=re.I)[0]
    if isinstance(v, list):
        # 줄 안의 description 은 KEROL 로 안 가고 회차마다 잘리는 자리가 다르다. 수량과 금액만 견준다.
        v = [{k2: x for k2, x in it.items() if k2 != "description"} if isinstance(it, dict) else it for it in v]
    if isinstance(v, (list, dict)):
        return json.dumps(_num_norm(v), ensure_ascii=False, sort_keys=True)
    if v is None:
        return ""
    return str(_num_norm(v)).strip()


def _text_norm(t):
    """같은 값을 다르게 적은 것을 한 꼴로 만든다.

    잡음 5건에서 갈린 35자리를 셋으로 가르니 둘째가 이것이었다. `8 inch` 와 `8"`,
    `Incheon` 과 `INCHEON` 은 모델이 흔들린 것이 아니라 같은 값을 다르게 적은 것이다.
    이것을 갈렸다고 세면 진짜 흔들린 자리(56726.08 과 56048.97)가 그 사이에 묻힌다.

    전각도 민다. 일본 사무소의 서류는 번호와 낱말을 전각으로 찍는다.
    """
    t = unicodedata.normalize("NFKC", t).upper()
    t = re.sub(r'(\d)\s*(?:"|INCHES\b|INCH\b)', r"\1 INCH", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t


def _num_norm(v):
    """14 와 14.0 을 같게 만든다.

    첫 실측에서 갈린 21칸 중 여섯이 이것이었다. awb_gross 가 v3 은 14, v4 는 14.0
    이었는데 문자열로 견주니 다르다고 나왔다. 같은 값이다. 이대로 세면 프롬프트를
    바꿔서 값이 흔들린 것처럼 보이고, 실제로 흔들린 자리가 묻힌다.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, list):
        return [_num_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _num_norm(x) for k, x in v.items()}
    if isinstance(v, str):
        t = v.strip()
        try:
            f = float(t)
        except ValueError:
            return _text_norm(t)
        # "1583" 같은 번호를 수로 바꾸면 앞의 0 이 날아간다. 소수점이 있는 것만 민다.
        return int(f) if "." in t and f == int(f) else t
    return v


class CallFailed(Exception):
    """모델을 못 불렀다. 빈 결과로 견주면 100% 일치라는 거짓말이 나오므로 여기서 멈춘다."""


def one(bundle, profile, codes, model, how):
    docpipe.set_prompt(profile)
    rows = docpipe.run([bundle], codes, model=model, show=False, **how)
    r = rows[0]
    st = r.get("llm") or {}
    if st.get("error"):
        raise CallFailed(st["error"])
    if not r.get("raw"):
        raise CallFailed("모델이 아무것도 안 냈습니다")
    ex = docpipe.postprocess(r["raw"])
    if not any(flat(v) for v in ex.values()):
        raise CallFailed("모든 칸이 비었습니다")
    bad = [x for x in (r.get("verify") or []) if x[0] == "FAIL"]
    return ex, st, bad, r.get("verify") or []


def main(argv=None):
    ap = argparse.ArgumentParser(description="프롬프트 두 판을 같은 서류로 견준다.")
    ap.add_argument("--root", required=True, help="케이스 폴더들이 든 상위 폴더")
    ap.add_argument("--n", type=int, default=5, help="견줄 케이스 수. 케이스마다 두 번 부른다")
    ap.add_argument("--a", default="v3")
    ap.add_argument("--b", default="v4")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--claude-cli", action="store_true",
                    help="API 키 없이 Claude Code CLI 로 부른다. 스캔본도 읽는다")
    ap.add_argument("--a-think", action="store_true", help="a 쪽만 생각 토큰을 켠다")
    ap.add_argument("--b-think", action="store_true", help="b 쪽만 생각 토큰을 켠다")
    ap.add_argument("--claude-bin", default="claude",
                    help="claude 실행 파일 경로. PATH 에 없으면 `which claude` 가 알려 준 경로를 준다")
    ap.add_argument("--claude-config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR"),
                    help="CLI 설정 폴더. 다른 계정으로 돌릴 때 준다")
    ap.add_argument("--no-split", action="store_true", help="서류별로 나눠 부르지 않는다")
    ap.add_argument("--codes", default=os.path.join(
        os.path.dirname(HERE), "schema", "codes.json"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only-scan", action="store_true", help="글자층이 없는 서류만 고른다")
    ap.add_argument("--save", help="자세한 결과를 JSON 으로 낸다. 저장소에 넣지 마십시오")
    ap.add_argument("--report", help="요약 수치만 JSON 으로 낸다. tools/measure.py 가 이것을 읽는다")
    a = ap.parse_args(argv)

    base = ({"claude_cli": True, "claude_bin": a.claude_bin, "claude_config_dir": a.claude_config_dir,
             "split": not a.no_split} if a.claude_cli else {"anthropic": True})
    how_a = dict(base, **({"claude_think": True} if (a.a_think and a.claude_cli) else {}))
    how_b = dict(base, **({"claude_think": True} if (a.b_think and a.claude_cli) else {}))
    if not a.claude_cli and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 가 없습니다. 키 없이 돌리려면 --claude-cli 를 주십시오.")
    # 호출을 다 돌린 뒤에 없다고 알면 기다린 시간이 버려지므로 먼저 본다.
    if a.claude_cli and not (os.path.sep in a.claude_bin or shutil.which(a.claude_bin)):
        sys.exit(docpipe.cli_missing_message(a.claude_bin))
    picked = cases(a.root, a.n, a.seed, a.only_scan)
    if not picked:
        sys.exit(f"{a.root} 에서 케이스를 못 찾았습니다.")
    lab_a = f"{a.a}{'+생각' if a.a_think else ''}"
    lab_b = f"{a.b}{'+생각' if a.b_think else ''}"
    print(f"케이스 {len(picked)}건을 {lab_a} 와 {lab_b} 로 각각 돌립니다. 호출 {len(picked)*2}회."
          f"  ({'Claude Code CLI' if a.claude_cli else 'Anthropic API'})")
    if a.a == a.b and a.a_think == a.b_think:
        print("같은 판을 두 번 돌립니다. **여기서 나오는 불일치가 잡음 바닥입니다.**\n"
              "두 판을 견준 수치는 이 바닥보다 클 때만 판이 다르다는 뜻입니다.\n")
    else:
        print("먼저 --a 와 --b 를 같게 줘서 잡음 바닥을 재 두십시오. 그 값 없이는\n"
              "아래 불일치가 판 차이인지 회차 차이인지 못 가릅니다.\n")

    props_a = set(docpipe.PROFILES[a.a]()[0]["properties"])
    props_b = set(docpipe.PROFILES[a.b]()[0]["properties"])
    shared = sorted(props_a & props_b)
    extra = sorted(props_b - props_a)
    agree = differ = failed = 0
    sec_a = sec_b = 0.0
    tot_empty_a = tot_empty_b = 0
    per_key = {}
    detail = []
    for i, bundle in enumerate(picked, 1):
        name = bundle[0]
        print(f"[{i}/{len(picked)}] {name}")
        try:
            exa, sta, bada, va = one(bundle, a.a, a.codes, a.model, how_a)
            exb, stb, badb, vb = one(bundle, a.b, a.codes, a.model, how_b)
        except docpipe.RateLimited as e:
            print(f"    {e}\n    한도에 걸렸습니다. 남은 {len(picked) - i + 1}건은 부르지 않고 여기서 멈춥니다.")
            failed += len(picked) - i + 1
            break
        except CallFailed as e:
            print(f"    모델 호출 실패: {e}")
            failed += 1
            continue
        except Exception as e:
            print(f"    실패: {repr(e)[:200]}")
            failed += 1
            continue
        diffs = []
        empty_a = empty_b = 0
        for k in shared:
            x, y = flat(exa.get(k), k), flat(exb.get(k), k)
            if x and not y: empty_b += 1
            if y and not x: empty_a += 1
            if x == y:
                agree += 1
            else:
                differ += 1
                per_key[k] = per_key.get(k, 0) + 1
                diffs.append((k, x[:60], y[:60]))
        sec_a += (sta.get("wall") or 0) / 1000
        sec_b += (stb.get("wall") or 0) / 1000
        tot_empty_a += empty_a
        tot_empty_b += empty_b
        if empty_a or empty_b:
            print(f"    한쪽만 빈 칸: {lab_a} 가 빈 것 {empty_a}, {lab_b} 가 빈 것 {empty_b}")
        print(f"    공통 {len(shared)}칸 중 다른 값 {len(diffs)}칸"
              f" | FAIL {lab_a} {len(bada)} / {lab_b} {len(badb)}"
              f" | 초 {(sta.get('wall') or 0)/1000:.1f} vs {(stb.get('wall') or 0)/1000:.1f}")
        for k, x, y in diffs:
            print(f"      {k:<24} {lab_a}={x!r}  {lab_b}={y!r}")
        filled = [k for k in extra if flat(exb.get(k))]
        if extra:
            print(f"      {lab_b} 가 더 뽑는 {len(extra)}칸 중 값이 찬 것 {len(filled)}: {', '.join(filled) or '없음'}")
        detail.append({"case": name, "diffs": diffs, "b_extra_filled": filled,
                       "fail_a": [list(t) for t in bada], "fail_b": [list(t) for t in badb],
                       "stat_a": sta, "stat_b": stb})

    tot = agree + differ
    if a.report:
        # 값은 안 넣고 수치만 넣는다. 서류 내용이 저장소로 새지 않게 한다.
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"a": lab_a, "b": lab_b, "model": a.model, "cases": len(picked),
                       "failed": failed, "agree": agree, "differ": differ,
                       "pct": round(100 * agree / tot, 1) if tot else None,
                       "control": a.a == a.b and a.a_think == a.b_think,
                       "per_key": per_key, "sec_a": round(sec_a, 1), "sec_b": round(sec_b, 1),
                       "empty_a": tot_empty_a, "empty_b": tot_empty_b}, f, ensure_ascii=False, indent=1)
    print(f"\n=== 정리 ===")
    if failed:
        print(f"케이스 {failed}건이 호출에 실패해 빠졌습니다.")
    if not tot:
        print("견준 자리가 없습니다. 위의 실패 이유부터 보십시오.")
        return 1
    print(f"공통 칸 비교 {tot}자리 중 같은 값 {agree} ({100*agree/tot:.1f}%), 다른 값 {differ}")
    if per_key:
        print(f"\n갈린 칸 (많은 순)")
        for k, n in sorted(per_key.items(), key=lambda x: -x[1]):
            print(f"  {k:<24} {n}건")
    else:
        print("\n공통 칸은 두 번 다 같은 값이 나왔습니다.")
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=1)
        print(f"\n자세한 결과: {a.save}  (저장소에 넣지 마십시오)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
