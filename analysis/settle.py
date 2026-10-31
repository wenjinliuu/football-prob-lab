"""赛后结算：读取 recommendations/*.json 和体彩赛果，生成 reports/ledger.md。

- 推荐票账本：每张票是否中奖、净收益。取消/退款的比赛按竞彩规则以 SP 1.00 计。
- CLV：推荐时的 SP 与停售终赔比较（终赔来自赛果接口，只有胜平负）。推荐价长期高于终赔，说明买到了「便宜货」。
- 影子账本：每天所有 EV ≥ 0 的选项（胜平负和让球）各记 1 元，样本积累更快，用来检验方法本身。
推荐文件只读，不修改。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import load
from step1_market import ci, pct, table

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "recommendations"
OUT = ROOT / "reports" / "ledger.md"
PICK = {"主胜": "H", "平": "D", "客胜": "A"}


def leg_result(leg, res):
    """返回 (状态, 结算 SP)。状态：win / lose / refund / pending。"""
    r = res.get(leg["match_id"])
    if r is None:
        return "pending", None
    if r["pool_status"] == "Refund":
        return "refund", 1.0
    if r["home_goals"] != r["home_goals"]:
        return "pending", None
    want = PICK[leg["pick"]]
    if leg["pool"] == "胜平负":
        got = r["had_result"]
    else:
        gl = float(leg["pool"].split("(")[1].rstrip(")"))
        d = r["home_goals"] + gl - r["away_goals"]
        got = "H" if d > 0 else "D" if d == 0 else "A"
    return ("win", leg["sp"]) if got == want else ("lose", 0.0)


def main():
    m = load.matches()
    res = {int(r["match_id"]): r for r in m.to_dict("records")}
    recs = [json.loads(f.read_text()) for f in sorted(REC.glob("*.json"))] if REC.exists() else []
    tix, shadow, clv = [], [], []
    for rec in recs:
        for t in rec["tickets"]:
            states = [leg_result(l, res) for l in t["legs"]]
            if any(s == "lose" for s, _ in states):
                status, ret = "未中", 0.0
            elif any(s == "pending" for s, _ in states):
                status, ret = "待开奖", None
            else:
                status, ret = "中奖", t["stake"] * float(np.prod([sp for _, sp in states]))
            tix.append({"日期": rec["date"], "类型": t["type"], "内容": " × ".join(f"{l['num']}{l['pick']}@{l['sp']:.2f}" for l in t["legs"]),
                        "投入": t["stake"], "预测 EV": pct(t["ev"]), "结果": status, "返还": "—" if ret is None else f"{ret:.2f}",
                        "_stake": t["stake"], "_ret": ret, "_tier": t.get("tier")})
            for l in t["legs"]:
                r = res.get(l["match_id"])
                if l["pool"] == "胜平负" and r is not None and r["had_h"] == r["had_h"]:
                    final = r[{"H": "had_h", "D": "had_d", "A": "had_a"}[PICK[l["pick"]]]]
                    clv.append(l["sp"] / final - 1)
        for l in rec["legs"]:
            if l["ev"] < 0:
                continue
            s, sp = leg_result(l, res)
            if s != "pending":
                shadow.append({"date": rec["date"], "ev": l["ev"], "ret": (sp if s != "lose" else 0.0) - 1})

    md = [f"# 推荐账本\n\n> 自动生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC。只做模拟记录，不接入投注。\n\n"]
    done = [t for t in tix if t["_ret"] is not None]
    stake = sum(t["_stake"] for t in done)
    ret = sum(t["_ret"] for t in done)
    md.append("## 汇总\n\n")
    md.append(f"- 推荐天数 {len(recs)}，票数 {len(tix)}（已结算 {len(done)}，待开奖 {len(tix) - len(done)}）。\n")
    md.append(f"- 已结算投入 {stake} 元，返还 {ret:.2f} 元，净 {ret - stake:+.2f} 元" + (f"，ROI {pct((ret - stake) / stake)}" if stake else "") + "。\n")
    for tier, name in (("formal", "正式推荐"), ("fun", "娱乐串关")):
        d = [t for t in done if t["_tier"] == tier]
        s = sum(t["_stake"] for t in d)
        if s:
            md.append(f"- {name}：{len(d)} 张，投入 {s} 元，净 {sum(t['_ret'] for t in d) - s:+.2f} 元。\n")
    if clv:
        md.append(f"- CLV（推荐时 SP 相对停售终赔）：平均 {pct(np.mean(clv))}，{len(clv)} 条腿；正数说明推荐时买到的价格比最后更好。\n")
    if shadow:
        r = np.array([x["ret"] for x in shadow])
        lo, hi = ci(r)
        md.append(f"- 影子账本（所有 EV ≥ 0 的选项各 1 元）：{len(r)} 注，ROI {pct(r.mean())}" +
                  (f"（95% 区间 {pct(lo)} ~ {pct(hi)}）" if lo == lo else "（样本太少）") + "。\n")
    md.append("\n## 明细\n\n")
    if tix:
        md.append(table([{k: v for k, v in t.items() if not k.startswith("_")} for t in reversed(tix)], "日期"))
    else:
        md.append("还没有推荐记录。\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
