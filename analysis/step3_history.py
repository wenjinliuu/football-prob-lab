"""第 3 步：用变赔历史回答三个问题。

1. 让球胜平负的比分模型准不准：用 Pinnacle 胜平负概率反推两队预期进球（泊松），算体彩整数让球的三项概率，
   和体彩自己的让球赔率（去水）比，谁更准。
2. 胜平负 vs 让球：按 Pinnacle（及模型）概率算体彩 EV，哪个玩法的价差机会更多、实际回报更好。
3. 什么时候买：开盘价 / 比赛日上午价（每日推荐出单时间 10:17）/ 停售价，回报和 CLV 有何不同。
数据：体彩变赔历史（重点联赛）+ Football-Data 的 Pinnacle 赛前价（PSH）和收盘价（PSCH）。
输出 reports/step3_history.md。
"""
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import load
import match_fd
from step1_market import ci, pct, returns, table
from step2_pinnacle import scores, shin

OUT = Path(__file__).resolve().parent.parent / "reports" / "step3_history.md"
EV_BANDS = [-1, -0.10, -0.05, 0, 0.05, 0.10, 10]
GOAL_LINES = np.arange(-4, 5)


# ---------- 泊松比分模型（预先算好网格，按胜平负概率找最近的一组 λ） ----------

def _grid():
    lam = np.arange(0.2, 4.01, 0.05)
    k = np.arange(11)
    fact = np.array([math.factorial(i) for i in k], float)
    pk = np.exp(-lam[:, None]) * lam[:, None] ** k / fact          # (L, 11)
    mtx = pk[:, None, :, None] * pk[None, :, None, :]                # (L, L, 11, 11)
    mtx = mtx.reshape(-1, 11, 11)
    i, j = np.indices((11, 11))
    had = np.stack([mtx[:, i > j].sum(1), mtx[:, i == j].sum(1), mtx[:, i < j].sum(1)], 1)
    hhad = np.zeros((len(mtx), len(GOAL_LINES), 3))
    for g, gl in enumerate(GOAL_LINES):
        d = i + gl - j
        hhad[:, g] = np.stack([mtx[:, d > 0].sum(1), mtx[:, d == 0].sum(1), mtx[:, d < 0].sum(1)], 1)
    lh, la = np.meshgrid(lam, lam, indexing="ij")
    return had, hhad, lh.ravel(), la.ravel()


HAD_G, HHAD_G, LH, LA = _grid()


def hhad_from_had(p_had, goal_line):
    """p_had: (n,3)；goal_line: (n,) 整数让球。返回 (n,3) 让球三项概率。"""
    idx = np.empty(len(p_had), int)
    for s in range(0, len(p_had), 2000):
        d = ((p_had[s:s + 2000, None, :] - HAD_G[None]) ** 2).sum(-1)
        idx[s:s + 2000] = d.argmin(1)
    g = np.clip(goal_line.astype(int) + 4, 0, len(GOAL_LINES) - 1)
    return HHAD_G[idx, g]


# ---------- 数据准备 ----------

def snapshots(h, m):
    """每场每个玩法：初赔、比赛日 10:17 前最后一次（上午价）、终赔。"""
    kick = m.set_index("match_id")["date"]
    h = h.assign(morning=h["match_id"].map(kick) + pd.Timedelta(hours=10, minutes=17))
    out = {}
    for pool in ("had", "hhad"):
        hp = h[h["pool"] == pool]
        first = hp[hp["seq"] == 0].set_index("match_id")
        final = hp[hp["is_final"]].set_index("match_id")
        before = hp[hp["ts"] <= hp["morning"]].sort_values("ts").groupby("match_id").tail(1).set_index("match_id")
        out[pool] = {"初赔": first, "上午价": before, "终赔": final}
    return out


def outcome(hg, ag, gl=0.0):
    d = hg + gl - ag
    return np.select([d > 0, d == 0], [0, 1], 2)


def options(df, sp_cols, p, y, extra=None):
    rows = []
    sp = df[sp_cols].to_numpy(float)
    for j, o in enumerate(("主胜", "平", "客胜")):
        r = {"match_id": df["match_id"].to_numpy(), "year": df["year"].to_numpy(), "pick": o,
             "sp": sp[:, j], "p": p[:, j], "win": y == j}
        if extra:
            r.update({k: v[:, j] for k, v in extra.items()})
        rows.append(pd.DataFrame(r))
    s = pd.concat(rows, ignore_index=True)
    s["ev"] = s["p"] * s["sp"] - 1
    return s


def ev_table(sel):
    sel = sel.assign(band=pd.cut(sel["ev"], EV_BANDS, right=False))
    rows = []
    for b, g in sel.groupby("band", observed=True):
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        lo, hi = ci(r)
        label = f"低于 {pct(b.right, 0)}" if b.left <= -1 else (f"{pct(b.left, 0)} 以上" if b.right >= 10 else f"{pct(b.left, 0)} ~ {pct(b.right, 0)}")
        rows.append({"预测 EV": label, "注数": len(g), "平均 SP": f"{g['sp'].mean():.2f}", "预测 EV 均值": pct(g["ev"].mean()),
                     "实际 ROI": pct(r.mean()), "95% 区间": f"{pct(lo)} ~ {pct(hi)}" if lo == lo else "样本太少"})
    return table(rows, "预测 EV")


def rule_rows(name, sel, n_matches):
    out = []
    for rule, g in (("EV ≥ 0", sel[sel["ev"] >= 0]), ("EV ≥ 5% 且 SP ≤ 6", sel[(sel["ev"] >= 0.05) & (sel["sp"] <= 6)])):
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        lo, hi = ci(r)
        out.append({"玩法 · 规则": f"{name} · {rule}", "注数": len(g), "每 100 场有几注": f"{100 * len(g) / max(1, n_matches):.1f}",
                    "命中": pct(g["win"].mean()) if len(g) else "—", "实际 ROI": pct(r.mean()) if len(g) else "—",
                    "95% 区间": f"{pct(lo)} ~ {pct(hi)}" if lo == lo else "样本太少"})
    return out


def main():
    m = load.matches()
    h = load.odds_history()
    sf = load.single_flags()
    snap = snapshots(h, m)
    st, fd, alias, x = match_fd.run(write=False)
    x = x[x["score_ok"]].dropna(subset=["PSCH", "PSCD", "PSCA"])
    x = x[x["match_id"].isin(snap["hhad"]["终赔"].index)]

    md = [f"# 第 3 步：变赔历史分析（让球模型、玩法对比、买入时机）\n\n",
          f"> 自动生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC。变赔历史 {h['match_id'].nunique()} 场（五大联赛 + 欧冠/欧罗巴/欧协联，"
          f"{h['ts'].min():%Y-%m} 至 {h['ts'].max():%Y-%m}），其中与 Pinnacle 收盘价匹配 {len(x)} 场（仅五大联赛）。"
          "只做研究，不构成投注建议。\n\n"]
    concl = []

    # ---- 0. 概况 ----
    md.append("## 0. 体彩赔率变动概况\n\n")
    rows = []
    for pool, name in (("had", "胜平负"), ("hhad", "让球胜平负")):
        n = h[h["pool"] == pool].groupby("match_id").size()
        f0, f1 = snap[pool]["初赔"], snap[pool]["终赔"]
        pay0 = (1 / (1 / f0[["h", "d", "a"]]).sum(1)).median()
        pay1 = (1 / (1 / f1[["h", "d", "a"]]).sum(1)).median()
        single = sf[f"{pool}_single"].mean()
        rows.append({"玩法": name, "场次": len(n), "平均变赔次数": f"{n.mean():.1f}", "初赔返还率": pct(pay0), "终赔返还率": pct(pay1),
                     "可单关比例": pct(single)})
    md.append(table(rows, "玩法") + "\n")
    recent = sf.merge(m[["match_id", "year"]], on="match_id")
    recent = recent[recent["year"] >= recent["year"].max() - 1]
    md.append(f"最近两年：胜平负可单关 {pct(recent['had_single'].mean())}，让球可单关 {pct(recent['hhad_single'].mean())}。\n\n")

    # ---- 1. 让球模型验证 ----
    hf = snap["hhad"]["终赔"]
    d = x.join(hf[["h", "d", "a", "goal_line"]].add_prefix("hh_"), on="match_id").dropna(subset=["hh_h", "hh_d", "hh_a", "hh_goal_line"])
    gl = d["hh_goal_line"].to_numpy()
    y_hh = outcome(d["home_goals"].to_numpy(), d["away_goals"].to_numpy(), gl)
    y_had = outcome(d["home_goals"].to_numpy(), d["away_goals"].to_numpy())
    p_pin = shin(d[["PSCH", "PSCD", "PSCA"]].to_numpy(float))
    p_model = hhad_from_had(p_pin, gl)
    p_market = shin(d[["hh_h", "hh_d", "hh_a"]].to_numpy(float))
    md.append("## 1. 让球模型准不准\n\n")
    md.append(f"{len(d)} 场五大联赛。用 Pinnacle 收盘胜平负概率反推预期进球（泊松模型），算体彩整数让球的主/平/客概率，"
              "和体彩让球终赔去水后的概率比。数值越小越准。\n\n")
    srcs = {"体彩让球终赔（去水）": p_market, "模型 ← Pinnacle 收盘": p_model}
    valid = d["had_h"].notna().to_numpy()
    rows, base = [], scores(p_market, y_hh)[0]
    for name, p in srcs.items():
        ll, br, rps = scores(p, y_hh)
        diff = ll - base
        se = diff.std(ddof=1) / math.sqrt(len(diff))
        rows.append({"概率来源": name, "Log Loss": f"{ll.mean():.4f}", "Brier": f"{br.mean():.4f}", "RPS": f"{rps.mean():.4f}",
                     "对体彩差值": "—" if name.startswith("体彩") else f"{diff.mean():+.4f} ± {1.96 * se:.4f}"})
    md.append(table(rows, "概率来源") + "\n")
    diff = scores(p_model, y_hh)[0] - base
    model_ok = diff.mean() <= 0 or abs(diff.mean()) < 1.96 * diff.std(ddof=1) / math.sqrt(len(diff))
    concl.append(f"让球模型（Pinnacle → 泊松）对比体彩让球终赔：Log Loss 差 {diff.mean():+.4f}，"
                 + ("不比体彩差，可以用来给让球选项算概率。" if model_ok else "比体彩差，让球暂不进推荐。"))

    cal = pd.DataFrame({"p": p_model.ravel(), "win": (np.eye(3)[y_hh]).ravel().astype(bool)})
    cal["band"] = pd.cut(cal["p"], [0, .1, .2, .3, .4, .5, .6, .7, .8, 1], right=False)
    rows = [{"模型概率": f"{b.left:.0%}–{b.right:.0%}", "注数": len(g), "模型": pct(g["p"].mean()), "实际": pct(g["win"].mean()),
             "差（实际−模型）": pct(g["win"].mean() - g["p"].mean())} for b, g in cal.groupby("band", observed=True)]
    md.append("**模型校准**\n\n" + table(rows, "模型概率") + "\n")

    # ---- 2. 胜平负 vs 让球 ----
    md.append("## 2. 胜平负 vs 让球：哪个价差机会多\n\n")
    md.append("都用 Pinnacle 收盘概率（让球经泊松模型换算），乘以体彩**终赔**算 EV。\n\n")
    dh = d[valid]
    sel_had = options(dh, ["had_h", "had_d", "had_a"], shin(dh[["PSCH", "PSCD", "PSCA"]].to_numpy(float)), y_had[valid])
    sel_hh = options(d, ["hh_h", "hh_d", "hh_a"], p_model, y_hh)
    md.append("**胜平负**\n\n" + ev_table(sel_had) + "\n**让球胜平负**\n\n" + ev_table(sel_hh) + "\n")
    rows = rule_rows("胜平负", sel_had, len(dh)) + rule_rows("让球", sel_hh, len(d))
    md.append(table(rows, "玩法 · 规则") + "\n")
    for name, s in (("胜平负", sel_had), ("让球", sel_hh)):
        g = s[(s["ev"] >= 0.05) & (s["sp"] <= 6)]
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        concl.append(f"{name}：EV ≥ 5% 的机会每 100 场约 {100 * len(g) / max(1, s['match_id'].nunique()):.1f} 注，按终赔实际 ROI {pct(r.mean()) if len(g) else '—'}。")

    # ---- 3. 什么时候买 ----
    md.append("## 3. 什么时候买：开盘 / 上午 / 停售\n\n")
    md.append("「上午价」= 比赛日北京时间 10:17（每日推荐出单时间）之前最后一次变赔。CLV = 该时刻 SP ÷ 停售 SP − 1，"
              "正数说明比停售时买划算。\n\n")
    rows = []
    for pool, name in (("had", "胜平负"), ("hhad", "让球")):
        base = m.set_index("match_id")
        for label in ("初赔", "上午价", "终赔"):
            s = snap[pool][label]
            fin = snap[pool]["终赔"]
            j = s.join(fin[["h", "d", "a"]].add_suffix("_f")).join(base[["home_goals", "away_goals"]]).dropna(subset=["home_goals", "h"])
            glv = j["goal_line"].fillna(0).to_numpy() if pool == "hhad" else 0.0
            yy = outcome(j["home_goals"].to_numpy(), j["away_goals"].to_numpy(), glv)
            sp = j[["h", "d", "a"]].to_numpy(float)
            clv = (sp / j[["h_f", "d_f", "a_f"]].to_numpy(float) - 1).ravel()
            allr = np.concatenate([returns(sp[:, k], yy == k) for k in range(3)])
            rows.append({"玩法 · 时点": f"{name} · {label}", "场次": len(j), "返还率中位数": pct(np.median(1 / (1 / sp).sum(1))),
                         "每注都买的 ROI": pct(allr.mean()), "平均 CLV": pct(np.nanmean(clv))})
    md.append(table(rows, "玩法 · 时点") + "\n")

    # 上午价 × 当时可得的 Pinnacle 赛前价（PSH，近似同一时刻）
    mo = snap["had"]["上午价"]
    dd = x.join(mo[["h", "d", "a"]].add_prefix("am_"), on="match_id").dropna(subset=["am_h", "PSH", "PSD", "PSA"])
    p_open = shin(dd[["PSH", "PSD", "PSA"]].to_numpy(float))
    y_am = outcome(dd["home_goals"].to_numpy(), dd["away_goals"].to_numpy())
    fin = dd[["had_h", "had_d", "had_a"]].to_numpy(float)
    sel_am = options(dd, ["am_h", "am_d", "am_a"], p_open, y_am, extra={"sp_final": fin})
    sel_am["clv"] = sel_am["sp"] / sel_am["sp_final"] - 1
    md.append("**更接近实际的检验：上午的体彩价 × Pinnacle 赛前价（PSH）**\n\n"
              "这模拟每日推荐的真实情况：上午看到的体彩 SP，配上较早的国际价格（不偷看收盘）。\n\n")
    md.append(ev_table(sel_am) + "\n")
    g = sel_am[(sel_am["ev"] >= 0.05) & (sel_am["sp"] <= 6)]
    r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
    lo, hi = ci(r)
    md.append(f"EV ≥ 5% 且 SP ≤ 6：{len(g)} 注，实际 ROI {pct(r.mean()) if len(g) else '—'}"
              + (f"（95% 区间 {pct(lo)} ~ {pct(hi)}）" if lo == lo else "") + f"，这些选项的平均 CLV {pct(g['clv'].mean()) if len(g) else '—'}。\n\n")
    concl.append(f"按每日推荐的真实时点（上午体彩价 × 较早的 Pinnacle 价）筛 EV ≥ 5%：{len(g)} 注，ROI {pct(r.mean()) if len(g) else '—'}"
                 + (f"（95% 区间 {pct(lo)} ~ {pct(hi)}）" if lo == lo else "") + "。")
    hi_ev = sel_am[sel_am["ev"] >= 0]
    concl.append(f"上午 EV ≥ 0 的选项，到停售时体彩 SP 平均变化 {pct(hi_ev['clv'].mean())}（正数 = 停售前体彩把价格降了，上午买更划算）。")

    md.insert(2, "## 结论（先看这里）\n\n" + "".join(f"- {c}\n" for c in concl) + "\n")
    OUT.write_text("".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
