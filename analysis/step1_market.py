"""第 1 步：体彩赔率本身准不准、各种简单策略是否亏损。

只用体彩官方数据：
- 赛果接口：全部联赛的胜平负（HAD）终赔 + 比分。
- 变赔接口（重点联赛）：HAD / HHAD 的初赔和终赔。
输出 reports/step1_market.md。用法：python analysis/step1_market.py
"""
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import load

OUT = Path(__file__).resolve().parent.parent / "reports" / "step1_market.md"
RNG = np.random.default_rng(20261031)
SP_BANDS = [1.0, 1.3, 1.6, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 12.0, 1000.0]
P_BANDS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
NAME = {"H": "主胜", "D": "平", "A": "客胜"}


# ---------- 通用统计 ----------

def returns(sp, win):
    """每注 1 元的净收益：中了 SP-1，没中 -1。"""
    return np.where(win, sp - 1.0, -1.0)


def ci(r, n_boot=2000):
    """ROI 的 95% bootstrap 区间。"""
    r = np.asarray(r, float)
    if len(r) < 30:
        return float("nan"), float("nan")
    idx = RNG.integers(0, len(r), size=(n_boot, len(r)))
    m = r[idx].mean(axis=1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def p_positive(r):
    """单侧检验 ROI > 0 的 p 值（正态近似）。"""
    r = np.asarray(r, float)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 1.0
    z = r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))
    return 0.5 * math.erfc(z / math.sqrt(2))


def bh(pvals, q=0.10):
    """Benjamini-Hochberg：返回每个检验是否显著。"""
    p = np.asarray(pvals, float)
    order = np.argsort(p)
    thresh = q * np.arange(1, len(p) + 1) / len(p)
    passed = p[order] <= thresh
    k = np.max(np.nonzero(passed)[0]) + 1 if passed.any() else 0
    sig = np.zeros(len(p), bool)
    sig[order[:k]] = True
    return sig


def pct(x, d=1):
    return "—" if x != x else f"{x * 100:.{d}f}%"


def summarize(r, win, p_impl=None):
    lo, hi = ci(r)
    row = {"注数": len(r), "命中": pct(np.mean(win)) if len(r) else "—"}
    if p_impl is not None:
        row["隐含概率"] = pct(np.mean(p_impl)) if len(r) else "—"
    row["ROI"] = pct(np.mean(r)) if len(r) else "—"
    row["95% 区间"] = f"{pct(lo)} ~ {pct(hi)}" if lo == lo else "样本太少"
    return row


def table(rows, index_name):
    df = pd.DataFrame(rows)
    cols = [index_name] + [c for c in df.columns if c != index_name]
    df = df[cols]
    head = "| " + " | ".join(cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n"
    return head + "".join("| " + " | ".join(str(v) for v in r) + " |\n" for r in df.itertuples(index=False))


# ---------- 把每场三项展开成"选项" ----------

def selections(df, cols, result_col):
    """每场 3 行：选项、SP、去水概率、是否命中、在本场的赔率排名（0=热门）。"""
    sp = df[cols].to_numpy(float)
    inv = 1.0 / sp
    p = inv / inv.sum(axis=1, keepdims=True)
    rank = np.argsort(np.argsort(sp, axis=1, kind="stable"), axis=1)
    rows = []
    for j, o in enumerate("HDA"):
        rows.append(pd.DataFrame({
            "match_id": df["match_id"].to_numpy(),
            "year": df["year"].to_numpy(),
            "pick": o,
            "sp": sp[:, j],
            "p": p[:, j],
            "rank": rank[:, j],
            "win": (df[result_col] == o).to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


def margin_section(df, cols, title):
    pay = 1.0 / (1.0 / df[cols]).sum(axis=1)
    by_year = pay.groupby(df["year"]).median()[df.groupby("year").size() >= 30]
    s = f"**{title}**：返还率中位数 {pct(pay.median())}（抽水约 {pct(1 - pay.median())}），场次 {len(df)}。\n\n"
    s += "| 年份 | " + " | ".join(str(y) for y in by_year.index) + " |\n"
    s += "| --- | " + " | ".join("---" for _ in by_year.index) + " |\n"
    s += "| 返还率 | " + " | ".join(pct(v) for v in by_year.values) + " |\n\n"
    return s


def calibration_section(sel):
    rows = []
    sel = sel.assign(band=pd.cut(sel["p"], P_BANDS, right=False))
    for b, g in sel.groupby("band", observed=True):
        rows.append({"去水隐含概率": f"{b.left:.0%}–{b.right:.0%}", "注数": len(g),
                     "隐含": pct(g["p"].mean()), "实际命中": pct(g["win"].mean()),
                     "差（实际−隐含）": pct(g["win"].mean() - g["p"].mean())})
    return table(rows, "去水隐含概率")


def band_section(sel):
    """按 SP 区间 × 选项（主/平/客）统计每注 ROI，并做 BH 多重检验。"""
    sel = sel.assign(band=pd.cut(sel["sp"], SP_BANDS, right=False))
    cells = []
    for (b, o), g in sel.groupby(["band", "pick"], observed=True):
        if len(g) < 50:
            continue
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        cells.append((b, o, g, r, p_positive(r)))
    sig = bh([c[4] for c in cells]) if cells else []
    rows = []
    for (b, o, g, r, pv), s in zip(cells, sig):
        right = "以上" if b.right >= 1000 else f"–{b.right:g}"
        row = {"SP 区间": f"{b.left:g}{right}", "选项": NAME[o]}
        row.update(summarize(r, g["win"].to_numpy(), g["p"].to_numpy()))
        row["显著为正"] = "是" if s else "否"
        rows.append(row)
    n_sig = int(np.sum(sig)) if len(cells) else 0
    return table(rows, "SP 区间"), len(cells), n_sig


def strategy_section(sel, label):
    """每场只买一注：热门（最低 SP）、中间、冷门（最高 SP），以及固定买主/平/客。"""
    rows = []
    for name, g in [("热门", sel[sel["rank"] == 0]), ("中间", sel[sel["rank"] == 1]), ("冷门", sel[sel["rank"] == 2]),
                    ("总买主胜", sel[sel["pick"] == "H"]), ("总买平", sel[sel["pick"] == "D"]), ("总买客胜", sel[sel["pick"] == "A"])]:
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        row = {"策略": name}
        row.update(summarize(r, g["win"].to_numpy(), g["p"].to_numpy()))
        rows.append(row)
    return f"**{label}**\n\n" + table(rows, "策略")


def yearly_section(sel):
    rows = []
    for y, gy in sel.groupby("year"):
        if gy["match_id"].nunique() < 200:  # 样本太少的年份（如刚开始采集的当年）不列
            continue
        row = {"年份": y, "场次": gy["match_id"].nunique()}
        for name, rk in (("热门", 0), ("中间", 1), ("冷门", 2)):
            g = gy[gy["rank"] == rk]
            row[name] = pct(np.mean(returns(g["sp"].to_numpy(), g["win"].to_numpy())))
        rows.append(row)
    return table(rows, "年份")


# ---------- 初赔 vs 终赔（变赔接口） ----------

def history_frames(m, h):
    """返回 {(pool, '初赔'|'终赔'): 每场一行的 DataFrame}，并核对终赔与赛果接口是否一致。"""
    out, notes = {}, []
    base = m[m["pool_status"] == "Payout"][["match_id", "year", "league", "had_result", "home_goals", "away_goals"]]
    for pool in ("had", "hhad"):
        hp = h[h["pool"] == pool]
        if hp.empty:
            continue
        first = hp[hp["seq"] == 0]
        final = hp[hp["is_final"]]
        for label, x in (("初赔", first), ("终赔", final)):
            d = base.merge(x[["match_id", "h", "d", "a", "goal_line"]], on="match_id").dropna(subset=["h", "d", "a"])
            if pool == "hhad":
                d = d.dropna(subset=["goal_line"])
                diff = d["home_goals"] + d["goal_line"] - d["away_goals"]
                d["res"] = np.select([diff > 0, diff == 0], ["H", "D"], "A")
            else:
                d["res"] = d["had_result"]
            out[(pool, label)] = d
    if ("had", "终赔") in out:
        chk = out[("had", "终赔")].merge(m[["match_id", "had_h", "had_d", "had_a"]], on="match_id")
        same = (np.isclose(chk["h"], chk["had_h"]) & np.isclose(chk["d"], chk["had_d"]) & np.isclose(chk["a"], chk["had_a"])).mean()
        notes.append(f"变赔接口的 HAD 终赔与赛果接口一致的比例：{pct(same)}（{len(chk)} 场）。")
    return out, notes


def movement_section(first, final):
    """初赔→终赔，SP 下降（被追捧）或上升的选项，按终赔买入的 ROI。"""
    a = selections(first, ["h", "d", "a"], "res")
    b = selections(final, ["h", "d", "a"], "res")
    x = a[["match_id", "pick", "sp"]].rename(columns={"sp": "sp0"}).merge(b, on=["match_id", "pick"])
    x["move"] = x["sp"] / x["sp0"] - 1
    rows = []
    for name, g in [("SP 降 ≥10%", x[x["move"] <= -0.10]), ("SP 降 3–10%", x[(x["move"] > -0.10) & (x["move"] <= -0.03)]),
                    ("基本不变 ±3%", x[x["move"].abs() < 0.03]), ("SP 升 3–10%", x[(x["move"] >= 0.03) & (x["move"] < 0.10)]),
                    ("SP 升 ≥10%", x[x["move"] >= 0.10])]:
        r = returns(g["sp"].to_numpy(), g["win"].to_numpy())
        row = {"初赔→终赔": name}
        row.update(summarize(r, g["win"].to_numpy(), g["p"].to_numpy()))
        rows.append(row)
    return table(rows, "初赔→终赔")


# ---------- 主程序 ----------

def main():
    m = load.matches()
    h = load.odds_history()
    had = m[(m["pool_status"] == "Payout")].dropna(subset=["had_h", "had_d", "had_a"])
    sel = selections(had, ["had_h", "had_d", "had_a"], "had_result")

    years = f"{had['year'].min()}–{had['year'].max()}"
    md = [f"# 第 1 步：体彩赔率本身准不准\n\n",
          f"> 自动生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC。数据：体彩官方赛果 {years}，"
          f"有效胜平负 {len(had)} 场；变赔历史 {h['match_id'].nunique()} 场（重点联赛）。\n"
          "> ROI = 每注 1 元的平均净收益；95% 区间用 bootstrap 估计。只做研究，不构成投注建议。\n\n"]

    # 结论区放最前面，下面再填
    concl = []

    md.append("## 1. 体彩抽多少水\n\n")
    md.append(margin_section(had, ["had_h", "had_d", "had_a"], "胜平负终赔（全部联赛）"))
    if len((hf := history_frames(m, h)[0]).get(("hhad", "终赔"), [])) >= 30:
        md.append(margin_section(hf[("hhad", "终赔")], ["h", "d", "a"], "让球胜平负终赔（重点联赛）"))
    paid = m[m["pool_status"] == "Payout"]
    single = paid.groupby("year")["single"].mean()[paid.groupby("year").size() >= 200]
    md.append("赛果接口「整场单关」标记的比例：" + "，".join(f"{y} 年 {pct(v, 0)}" for y, v in single.items()) +
              "。注意这不等于胜平负可单关；分玩法的单关信息以变赔历史为准。没开单关的场次只能串关购买。\n\n")
    sf = load.single_flags().merge(m[["match_id", "year"]], on="match_id")
    if len(sf) >= 200:
        g = sf.groupby("year").agg(n=("match_id", "size"), had=("had_single", "mean"), hhad=("hhad_single", "mean"))
        g = g[g["n"] >= 100]
        md.append("重点联赛按变赔历史统计的单关比例：" + "，".join(f"{y} 年胜平负 {pct(r.had, 0)}、让球 {pct(r.hhad, 0)}" for y, r in g.iterrows()) + "。\n\n")

    md.append("## 2. 赔率校准：标的概率和实际命中对得上吗\n\n")
    md.append("把每场的主/平/客三项都当作一个选项，按去水后的隐含概率分组，比较实际命中率。"
              "差值为正说明体彩低估了这一档，为负说明高估。\n\n")
    md.append(calibration_section(sel) + "\n")

    md.append("## 3. 每场买一注的简单策略（胜平负终赔）\n\n")
    md.append(strategy_section(sel, "全部年份") + "\n")
    md.append("**各年份 ROI**\n\n" + yearly_section(sel) + "\n")
    strat = {k: np.mean(returns(g["sp"].to_numpy(), g["win"].to_numpy()))
             for k, g in (("热门", sel[sel["rank"] == 0]), ("中间", sel[sel["rank"] == 1]), ("冷门", sel[sel["rank"] == 2]))}
    cis = {k: ci(returns(g["sp"].to_numpy(), g["win"].to_numpy()))
           for k, g in (("热门", sel[sel["rank"] == 0]), ("中间", sel[sel["rank"] == 1]), ("冷门", sel[sel["rank"] == 2]))}
    overlap = max(lo for lo, _ in cis.values()) <= min(hi for _, hi in cis.values())
    concl.append(f"每场买一注（含你原来的「中间赔率」）：热门 {pct(strat['热门'])}、中间 {pct(strat['中间'])}、冷门 {pct(strat['冷门'])}，"
                 + ("三者都亏，差别在误差范围内，基本就是体彩的抽水。" if overlap else f"都亏，其中「{max(strat, key=strat.get)}」亏得相对少。"))

    md.append("## 4. 按 SP 区间找有没有赚钱的档位\n\n")
    tbl, n_cells, n_sig = band_section(sel)
    md.append(f"共检验 {n_cells} 个「SP 区间 × 选项」组合（每组至少 50 注），用 BH 方法做多重检验校正（q = 10%），"
              "避免在很多组里碰巧挑出一个看起来赚钱的。\n\n" + tbl + "\n")
    concl.append(f"{n_cells} 个 SP 区间组合中，校正后显著为正的有 {n_sig} 个。" +
                 ("没有任何档位能稳定赚钱。" if n_sig == 0 else "这些档位值得在后续用国际赔率进一步验证，单靠这一项还不能下注。"))

    hf, notes = history_frames(m, h)
    md.append("## 5. 初赔 vs 终赔（重点联赛，来自变赔历史）\n\n")
    if not hf:
        md.append("变赔历史还在回补中，暂无数据。\n")
    else:
        md.extend(n + "\n\n" for n in notes)
        for pool, title in (("had", "胜平负"), ("hhad", "让球胜平负")):
            for label in ("初赔", "终赔"):
                d = hf.get((pool, label))
                if d is not None and len(d) >= 30:
                    md.append(strategy_section(selections(d, ["h", "d", "a"], "res"), f"{title} · 按{label}买") + "\n")
            if (pool, "初赔") in hf and len(hf[(pool, "终赔")]) >= 30:
                md.append(f"**{title} · 赔率变动方向（按终赔买）**\n\n" + movement_section(hf[(pool, "初赔")], hf[(pool, "终赔")]) + "\n")
        if len(hf.get(("had", "终赔"), [])) < 1000:
            md.append("> 变赔样本还少，这一节的结论先不作数，等回补完成后再看。\n\n")

    md.insert(2, "## 结论（先看这里）\n\n" + "".join(f"- {c}\n" for c in concl) + "\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
