"""第 2 步：用国际市场（Pinnacle）概率对照体彩，检验价差是否存在。

- 同一批比赛上比较：体彩去水概率 vs Pinnacle 去水概率，谁更准（Log Loss / Brier / RPS）。
- 价值检验：EV = Pinnacle 概率 × 体彩 SP − 1，按 EV 分组看体彩实际 ROI。
数据：体彩胜平负终赔 + Football-Data 的 Pinnacle 赛前价（PSH）与收盘价（PSCH）。只做胜平负。
输出 reports/step2_pinnacle.md。
"""
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import match_fd
from step1_market import ci, pct, returns, table

OUT = Path(__file__).resolve().parent.parent / "reports" / "step2_pinnacle.md"
EV_BANDS = [-1, -0.10, -0.05, 0, 0.05, 0.10, 10]


def shin(odds):
    """Shin 去水：考虑「冷门里内幕资金更多」的去水方法。odds: (n, 3)。"""
    q = 1.0 / np.asarray(odds, float)
    b = q.sum(axis=1, keepdims=True)
    lo, hi = np.zeros((len(q), 1)), np.full((len(q), 1), 0.4)
    for _ in range(60):
        z = (lo + hi) / 2
        p = (np.sqrt(z ** 2 + 4 * (1 - z) * q ** 2 / b) - z) / (2 * (1 - z))
        s = p.sum(axis=1, keepdims=True)
        hi, lo = np.where(s < 1, z, hi), np.where(s < 1, lo, z)
    return p / p.sum(axis=1, keepdims=True)


def mult(odds):
    q = 1.0 / np.asarray(odds, float)
    return q / q.sum(axis=1, keepdims=True)


def scores(p, y):
    """y: 0/1/2 = 主/平/客。返回每场的 logloss、brier、rps。"""
    onehot = np.eye(3)[y]
    ll = -np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))
    br = ((p - onehot) ** 2).sum(axis=1)
    rps = (((np.cumsum(p, 1) - np.cumsum(onehot, 1))[:, :2]) ** 2).sum(axis=1) / 2
    return ll, br, rps


def accuracy_section(d, y, sources):
    rows, base = [], None
    for name, p in sources.items():
        ll, br, rps = scores(p, y)
        if base is None:
            base = ll
        diff = ll - base
        se = diff.std(ddof=1) / math.sqrt(len(diff)) if len(diff) > 1 else float("nan")
        rows.append({"概率来源": name, "Log Loss": f"{ll.mean():.4f}", "Brier": f"{br.mean():.4f}", "RPS": f"{rps.mean():.4f}",
                     "Log Loss 差（对体彩）": "—" if name == next(iter(sources)) else f"{diff.mean():+.4f} ± {1.96 * se:.4f}"})
    return table(rows, "概率来源")


def value_section(sel):
    sel = sel.assign(band=pd.cut(sel["ev"], EV_BANDS, right=False))
    rows = []
    for b, g in sel.groupby("band", observed=True):
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        lo, hi = ci(r)
        left = "低于 " if b.left <= -1 else ""
        label = f"{left}{pct(b.right, 0)}" if b.left <= -1 else (f"{pct(b.left, 0)} 以上" if b.right >= 10 else f"{pct(b.left, 0)} ~ {pct(b.right, 0)}")
        rows.append({"预测 EV": label, "注数": len(g), "平均 SP": f"{g['sp'].mean():.2f}", "预测 EV 均值": pct(g["ev"].mean()),
                     "实际 ROI": pct(r.mean()), "95% 区间": f"{pct(lo)} ~ {pct(hi)}" if lo == lo else "样本太少"})
    return table(rows, "预测 EV")


def rule_section(sel):
    rows = []
    for name, g in [("EV ≥ 0", sel[sel["ev"] >= 0]), ("EV ≥ 5%", sel[sel["ev"] >= 0.05]),
                    ("EV ≥ 5% 且 SP ≤ 6", sel[(sel["ev"] >= 0.05) & (sel["sp"] <= 6)]), ("EV ≥ 5% 且 SP ≤ 6 且可单关", sel[(sel["ev"] >= 0.05) & (sel["sp"] <= 6) & sel["single"]]),
                    ("EV ≥ 10%", sel[sel["ev"] >= 0.10])]:
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        lo, hi = ci(r)
        rows.append({"规则": name, "注数": len(g), "占全部选项": pct(len(g) / len(sel)), "场次占比": pct(g["match_id"].nunique() / sel["match_id"].nunique()),
                     "命中": pct(g["win"].mean()) if len(g) else "—", "实际 ROI": pct(r.mean()) if len(g) else "—",
                     "95% 区间": f"{pct(lo)} ~ {pct(hi)}" if lo == lo else "样本太少"})
    return table(rows, "规则")


def main():
    st, fd, alias, x = match_fd.run()
    x = x[x["score_ok"]].dropna(subset=["had_h", "had_d", "had_a", "PSCH", "PSCD", "PSCA", "PSH", "PSD", "PSA"])
    y = x["had_result"].map({"H": 0, "D": 1, "A": 2}).to_numpy()
    st_odds = x[["had_h", "had_d", "had_a"]].to_numpy(float)
    ps_close = x[["PSCH", "PSCD", "PSCA"]].to_numpy(float)
    ps_open = x[["PSH", "PSD", "PSA"]].to_numpy(float)
    p_st, p_close, p_open = shin(st_odds), shin(ps_close), shin(ps_open)

    pay_st = 1 / (1 / st_odds).sum(1)
    pay_ps = 1 / (1 / ps_close).sum(1)
    years = f"{x['year'].min()}–{x['year'].max()}"
    md = [f"# 第 2 步：国际市场（Pinnacle）对照体彩\n\n",
          f"> 自动生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC。五大联赛 {years}，体彩与 Football-Data 匹配成功 {len(x)} 场"
          f"（体彩重点联赛 {len(st)} 场，队名对照 {len(alias)} 支，其中待复核 {(alias['status'] == 'review').sum()} 支）。\n"
          "> 去水用 Shin 方法。Pinnacle 赔率来自 Football-Data.co.uk（仅个人研究使用）。只做研究，不构成投注建议。\n\n"]
    concl = []

    md.append("## 1. 抽水对比\n\n")
    md.append(f"同一批比赛上：体彩胜平负返还率中位数 **{pct(np.median(pay_st))}**，Pinnacle 收盘 **{pct(np.median(pay_ps))}**。\n\n")

    md.append("## 2. 谁的概率更准\n\n")
    md.append("数值越小越准。「差」是相对体彩的 Log Loss 差值（负数 = 比体彩准），± 是 95% 区间。\n\n")
    md.append(accuracy_section(x, y, {"体彩终赔": p_st, "Pinnacle 赛前价": p_open, "Pinnacle 收盘价": p_close, "体彩（简单比例去水）": mult(st_odds)}) + "\n")
    d_ll = scores(p_close, y)[0] - scores(p_st, y)[0]
    concl.append(f"Pinnacle 收盘价比体彩终赔{'更准' if d_ll.mean() < 0 else '并不更准'}（Log Loss 差 {d_ll.mean():+.4f}）。")

    rows = []
    for j, o in enumerate("HDA"):
        rows.append(pd.DataFrame({"match_id": x["match_id"].to_numpy(), "year": x["year"].to_numpy(), "pick": o,
                                  "sp": st_odds[:, j], "p": p_close[:, j], "p_open": p_open[:, j], "win": y == j,
                                  "single": x["single"].to_numpy() == 1}))
    sel = pd.concat(rows, ignore_index=True)
    sel["ev"] = sel["p"] * sel["sp"] - 1
    sel_open = sel.assign(ev=sel["p_open"] * sel["sp"] - 1)

    md.append("## 3. 价差检验：按 Pinnacle 收盘概率算体彩的期望回报\n\n")
    md.append("每场主/平/客三个选项都算一次 EV = Pinnacle 概率 × 体彩 SP − 1，按预测 EV 分组，看体彩实际赚赔。"
              "如果 Pinnacle 概率可靠，预测 EV 越高的组，实际 ROI 也应越高。\n\n")
    md.append(value_section(sel) + "\n")
    md.append("**按规则筛选**\n\n" + rule_section(sel) + "\n")
    md.append("**改用 Pinnacle 赛前价（更早、更接近你实际下单的时间）**\n\n" + rule_section(sel_open) + "\n")
    g = sel[(sel["ev"] >= 0.05) & (sel["sp"] <= 6)]
    r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
    lo, hi = ci(r)
    per_day = len(g) / max(1, x["date"].nunique())
    concl.append(f"按「EV ≥ 5% 且 SP ≤ 6」筛选，{len(g)} 注、实际 ROI {pct(r.mean()) if len(g) else '—'}"
                 + (f"（95% 区间 {pct(lo)} ~ {pct(hi)}）" if lo == lo else "（样本太少，不作数）")
                 + f"，平均每个比赛日约 {per_day:.2f} 注。")
    concl.append("注意：这里用的是体彩终赔和 Pinnacle 收盘价，相当于开赛前最后一刻下单；你实际提前下单时价差会不同，"
                 "等变赔历史回补完再用当时的体彩 SP 复核。")

    md.append("## 4. 各年份\n\n")
    yr = []
    for yv, gy in sel.groupby("year"):
        if gy["match_id"].nunique() < 200:
            continue
        pick = gy[(gy["ev"] >= 0.05) & (gy["sp"] <= 6)]
        rr = returns(pick["sp"].to_numpy(), pick["win"].to_numpy())
        idx = x["year"].to_numpy() == yv
        yr.append({"年份": yv, "场次": gy["match_id"].nunique(),
                   "Log Loss 差": f"{(scores(p_close[idx], y[idx])[0] - scores(p_st[idx], y[idx])[0]).mean():+.4f}",
                   "EV≥5% 注数": len(pick), "EV≥5% ROI": pct(rr.mean()) if len(pick) else "—"})
    md.append(table(yr, "年份") + "\n")

    md.insert(2, "## 结论（先看这里）\n\n" + "".join(f"- {c}\n" for c in concl) + "\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
