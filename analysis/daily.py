"""每日推荐：体彩在售 SP × 国际市场（Pinnacle）概率 → 期望回报 → 推荐单。

流程：
1. 读最新一份体彩在售快照（calc）和最新一份 The Odds API 快照（h2h）。两份抓取时间相差须 ≤ 90 分钟。
2. 场次匹配：联赛对应 + 开球时间相差 ≤ 3 小时 + 英文队名相似度。
   中文队 → 英文名来自 data/mappings/team_alias_fd.csv（Football-Data 名）和 team_alias_oddsapi.csv（以往匹配成功的记录）。
3. 概率：胜平负用 Pinnacle 去水（Shin）。让球胜平负用泊松比分模型从胜平负概率反推（第 3 步验证：不比体彩让球赔率差）。
4. 选择：
   - 正式推荐（未验证）：胜平负或让球、EV ≥ 5%、SP ≤ 6；可单关出单关，否则满两个组 2 串 1；最多两张。
   - 娱乐串关：每天都出。每场取 EV ≥ −8% 里 EV 最高的一项，按 EV 排序，前 n 场组 n 串 1。2/3/4 串 1 用 SP 1.5–3.0 的稳腿，6/8 串 1 用 SP 2.2–4.0 的中高赔腿拉大奖金。
   - 每注 2 元，单日合计 ≤ 10 元。每条腿给出「底线 SP」：下单时低于它就别买。
5. 冻结：写 recommendations/YYYY-MM-DD.json 和 .md，附 sha256；同一天已存在则不覆盖。
只做研究和模拟，不接入投注。
"""
import hashlib
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

from step2_pinnacle import shin

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
MAPS = ROOT / "data" / "mappings"
OUT = ROOT / "recommendations"
BJ = timezone(timedelta(hours=8))

SPORT = {25: "soccer_epl", 62: "soccer_spain_la_liga", 40: "soccer_italy_serie_a", 37: "soccer_germany_bundesliga",
         32: "soccer_france_ligue_one", 69: "soccer_uefa_champs_league", 70: "soccer_uefa_europa_league",
         1033103: "soccer_uefa_europa_conference_league"}
NAME = {"H": "主胜", "D": "平", "A": "客胜"}

FORMAL_EV, FORMAL_MAX_SP = 0.05, 6.0
# 娱乐串关的腿：SP 1.5–3.0 的稳腿（用户 2026-10-31 确认 8 串 1 也用稳腿），EV ≥ -8%。
# 每场取 EV 最高的一项，再按 EV 从高到低排：小串只用最好的几场，大串依次往下补。
# 第 3 步回测：EV -5%~0 的选项实际只亏 1–4%；但每天每场最好的一项中位数约 -7.5%，门槛设 -5% 凑不齐 6/8 串。
FUN_MIN_EV, FUN_SP = -0.08, (1.5, 3.0)
# 6/8 串 1 用中高赔的腿拉大奖金（用户 2026-10-31 要求）：SP 2.2–4.0，同样按 EV 从高到低取。
BIG_PARLAY_MIN_N, BIG_SP = 6, (2.2, 4.0)
STAKE, DAILY_CAP = 2, 10
FUN_PARLAYS = (2, 3, 4, 6, 8)  # 竞彩胜平负/让球最多 8 串 1；用户 2026-10-31 要求
FUN_PRIORITY = (8, 6, 4, 3, 2)  # 预算不够时先保留大串（用户想要偶尔中个大的）


# ---------- 读快照 ----------

def latest_calc():
    f = sorted((RAW / "sporttery" / "calc").glob("*.json"), key=lambda p: p.name.split("__")[-1])[-1]
    raw = json.loads(f.read_text())
    fetched = pd.Timestamp(raw["fetchedAt"])
    rows = []
    for g in raw["data"]["value"]["matchInfoList"]:
        for m in g["subMatchList"]:
            single = {p["poolCode"]: int(p.get("single") or 0) for p in m.get("poolList") or []}
            had, hhad = m.get("had") or {}, m.get("hhad") or {}
            rows.append({
                "match_id": int(m["matchId"]), "num": m["matchNumStr"], "league_id": int(m["leagueId"]), "league": m["leagueAbbName"],
                "home": m["homeTeamAbbName"], "away": m["awayTeamAbbName"], "home_id": int(m["homeTeamId"]), "away_id": int(m["awayTeamId"]),
                "kickoff": pd.Timestamp(f'{m["matchDate"]} {m["matchTime"]}').tz_localize(BJ),
                "status": m.get("matchStatus"),
                "had_h": _f(had.get("h")), "had_d": _f(had.get("d")), "had_a": _f(had.get("a")),
                "hhad_h": _f(hhad.get("h")), "hhad_d": _f(hhad.get("d")), "hhad_a": _f(hhad.get("a")), "goal_line": _f(hhad.get("goalLine")),
                "had_single": single.get("HAD", 0), "hhad_single": single.get("HHAD", 0),
            })
    return f.name, fetched, pd.DataFrame(rows)


def latest_odds():
    files = sorted((RAW / "odds_api" / "h2h").glob("*.json"))
    stamp = max(f.name.split("__")[-1] for f in files)
    rows, fetched = [], None
    for f in files:
        if not f.name.endswith(stamp):
            continue
        rec = json.loads(f.read_text())
        fetched = pd.Timestamp(rec["fetchedAt"])
        if rec["status"] != 200:
            continue
        for e in rec["data"]:
            row = {"sport": rec["sport"], "event_id": e["id"], "o_home": e["home_team"], "o_away": e["away_team"],
                   "commence": pd.Timestamp(e["commence_time"])}
            prices = {}
            for b in e.get("bookmakers") or []:
                for mk in b.get("markets") or []:
                    if mk["key"] == "h2h":
                        o = {x["name"]: x["price"] for x in mk["outcomes"]}
                        prices[b["key"]] = (o.get(e["home_team"]), o.get("Draw"), o.get(e["away_team"]))
            row["pinnacle"] = prices.get("pinnacle")
            row["n_books"] = len(prices)
            # 备用：各家去水概率的中位数
            ps = [mult3(v) for v in prices.values() if all(v)]
            row["consensus"] = tuple(np.median(ps, axis=0)) if ps else None
            rows.append(row)
    return stamp, fetched, pd.DataFrame(rows)


def mult3(o):
    q = 1 / np.asarray(o, float)
    return q / q.sum()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


# ---------- 场次匹配 ----------

DROP = {"fc", "cf", "ac", "as", "sc", "afc", "bc", "fk", "sk", "nk", "rc", "ca", "tsg", "vfb", "fsv", "jk", "tc", "pfc", "the", "club", "de", "1"}
EXPAND = {"man": "manchester", "ath": "athletic", "nott'm": "nottingham", "ein": "eintracht", "m'gladbach": "monchengladbach",
          "sg": "saint germain", "st": "saint", "espanol": "espanyol", "inter": "inter milan", "milan": "ac milan"}


def norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    toks = [EXPAND.get(t, t) for t in re.split(r"[\s\-./]+", s) if t]
    return " ".join(t for t in toks if t not in DROP)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    tok = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(SequenceMatcher(None, a, b).ratio(), 0.9 * tok)


def load_aliases():
    names = {}
    fd = MAPS / "team_alias_fd.csv"
    if fd.exists():
        a = pd.read_csv(fd)
        names.update({int(t): n for t, n, s in zip(a["team_id"], a["fd"], a["status"]) if s == "ok"})
    known = {}
    oa = MAPS / "team_alias_oddsapi.csv"
    if oa.exists():
        b = pd.read_csv(oa)
        known = {int(t): n for t, n in zip(b["team_id"], b["oddsapi"])}
    return names, known


def team_score(team_id, oname, names, known):
    if team_id in known:
        return 1.0 if known[team_id] == oname else 0.0
    if team_id in names:
        return sim(names[team_id], oname)
    return None  # 未知球队


def match_events(st, ev):
    names, known = load_aliases()
    out = []
    for r in st.itertuples():
        sport = SPORT.get(r.league_id)
        if not sport:
            continue
        cand = ev[(ev["sport"] == sport) & ((ev["commence"] - r.kickoff).abs() <= pd.Timedelta(hours=3))]
        best = None
        for e in cand.itertuples():
            sh, sa = team_score(r.home_id, e.o_home, names, known), team_score(r.away_id, e.o_away, names, known)
            known_scores = [s for s in (sh, sa) if s is not None]
            if not known_scores:
                continue
            score = min(known_scores)
            close = abs((e.commence - r.kickoff).total_seconds()) <= 1800
            if score >= 0.6 or (score >= 0.45 and close):
                if best is None or score > best[0]:
                    best = (score, e, len(known_scores))
        if best:
            score, e, n_known = best
            # 只有一队已知时，要求时间窗内该联赛只有这一场能配上
            if n_known == 2 or len(cand) == 1 or score >= 0.85:
                out.append({"match_id": r.match_id, "event_id": e.event_id, "o_home": e.o_home, "o_away": e.o_away,
                            "match_score": round(score, 3)})
    return pd.DataFrame(out, columns=["match_id", "event_id", "o_home", "o_away", "match_score"])


def update_alias(st, m):
    """把本次匹配成功的球队记进 team_alias_oddsapi.csv（只新增）。"""
    f = MAPS / "team_alias_oddsapi.csv"
    old = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["team_id", "cn", "oddsapi", "first_seen"])
    x = st.merge(m, on="match_id")
    new = pd.concat([x[["home_id", "home", "o_home"]].set_axis(["team_id", "cn", "oddsapi"], axis=1),
                     x[["away_id", "away", "o_away"]].set_axis(["team_id", "cn", "oddsapi"], axis=1)])
    new = new[~new["team_id"].isin(old["team_id"])].drop_duplicates("team_id")
    if len(new):
        new["first_seen"] = datetime.now(BJ).strftime("%Y-%m-%d")
        MAPS.mkdir(parents=True, exist_ok=True)
        pd.concat([old, new]).to_csv(f, index=False)


# ---------- 让球：泊松比分模型（影子） ----------

def poisson_matrix(lh, la, n=11):
    k = np.arange(n)
    ph = np.exp(-lh) * lh ** k / np.array([math.factorial(i) for i in k])
    pa = np.exp(-la) * la ** k / np.array([math.factorial(i) for i in k])
    return np.outer(ph, pa)


def fit_lambdas(p):
    """找 (λ主, λ客) 使泊松模型的胜平负概率最接近 p。"""
    best = (1e9, 1.3, 1.1)
    for lh in np.arange(0.2, 4.01, 0.05):
        for la in np.arange(0.2, 4.01, 0.05):
            mtx = poisson_matrix(lh, la)
            q = (np.tril(mtx, -1).sum(), np.trace(mtx), np.triu(mtx, 1).sum())
            err = sum((a - b) ** 2 for a, b in zip(q, p))
            if err < best[0]:
                best = (err, lh, la)
    return best[1], best[2]


def hhad_probs(p_had, goal_line):
    lh, la = fit_lambdas(p_had)
    mtx = poisson_matrix(lh, la)
    i, j = np.indices(mtx.shape)
    d = i + goal_line - j
    return (mtx[d > 0].sum(), mtx[d == 0].sum(), mtx[d < 0].sum()), (lh, la)


# ---------- 主程序 ----------

def build(now=None, write_alias=True):
    now = now or datetime.now(timezone.utc)
    calc_file, calc_at, st = latest_calc()
    odds_stamp, odds_at, ev = latest_odds()
    gap = abs((calc_at - odds_at).total_seconds()) / 60
    st = st[(st["status"] == "Selling") & (st["kickoff"] > pd.Timestamp(now) + pd.Timedelta(minutes=30))]
    m = match_events(st, ev)
    if write_alias:
        update_alias(st, m)
    x = st.merge(m, on="match_id").merge(ev, on=["event_id", "o_home", "o_away"])

    legs = []
    for r in x.itertuples():
        src, p = ("pinnacle", shin(np.array([r.pinnacle]))[0]) if r.pinnacle and all(r.pinnacle) else \
                 ("consensus", np.asarray(r.consensus)) if r.consensus is not None else (None, None)
        if src is None:
            continue
        base = {"match_id": r.match_id, "num": r.num, "league": r.league, "home": r.home, "away": r.away,
                "kickoff": r.kickoff.strftime("%m-%d %H:%M"), "source": src, "n_books": r.n_books}
        for j, o in enumerate("HDA"):
            sp = getattr(r, f"had_{o.lower()}")
            if sp == sp:
                legs.append({**base, "pool": "胜平负", "pick": NAME[o], "sp": sp, "p": float(p[j]), "ev": float(p[j] * sp - 1),
                             "single": bool(r.had_single), "shadow": False})
        if r.goal_line == r.goal_line and r.hhad_h == r.hhad_h:
            ph, (lh, la) = hhad_probs(tuple(p), r.goal_line)
            for j, o in enumerate("HDA"):
                sp = getattr(r, f"hhad_{o.lower()}")
                legs.append({**base, "pool": f"让球({int(r.goal_line):+d})", "pick": NAME[o], "sp": sp, "p": float(ph[j]),
                             "ev": float(ph[j] * sp - 1), "single": bool(r.hhad_single), "shadow": False,
                             "lambda": [round(lh, 2), round(la, 2)]})
    legs = pd.DataFrame(legs)
    tickets = choose(legs) if len(legs) else []
    return {
        "date": now.astimezone(BJ).strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "inputs": {"sporttery_calc": calc_file, "sporttery_fetched_at": str(calc_at), "odds_api_stamp": odds_stamp,
                   "odds_fetched_at": str(odds_at), "gap_minutes": round(gap, 1)},
        "coverage": {"selling_focus": int(st["league_id"].isin(SPORT).sum()), "matched": int(len(m)),
                     "unmatched": st[st["league_id"].isin(SPORT) & ~st["match_id"].isin(m["match_id"])][["num", "league", "home", "away"]].to_dict("records")},
        "tickets": tickets,
        "legs": legs.round(4).to_dict("records") if len(legs) else [],
    }


def leg_view(l, min_ev):
    return {"match_id": int(l["match_id"]), "num": l["num"], "league": l["league"], "match": f'{l["home"]} vs {l["away"]}',
            "kickoff": l["kickoff"], "pool": l["pool"], "pick": l["pick"], "sp": l["sp"], "p": round(l["p"], 4),
            "ev": round(l["ev"], 4), "min_sp": round((1 + min_ev) / l["p"], 2)}


def ticket(kind, legs, min_ev):
    odds = float(np.prod([l["sp"] for l in legs]))
    p = float(np.prod([l["p"] for l in legs]))
    return {"type": kind, "stake": STAKE, "legs": [leg_view(l, min_ev) for l in legs], "odds": round(odds, 2),
            "payout": round(STAKE * odds, 2), "p_win": round(p, 4), "ev": round(p * odds - 1, 4)}


def choose(legs):
    """正式推荐（EV ≥ 5%，未验证）+ 娱乐 2/3/4/6/8 串 1。胜平负和让球都参与（让球模型第 3 步验证通过）。"""
    pool = legs.sort_values("ev", ascending=False)
    formal = pool[(pool["ev"] >= FORMAL_EV) & (pool["sp"] <= FORMAL_MAX_SP)].drop_duplicates("match_id")
    tickets = []
    for _, l in formal[formal["single"]].head(2).iterrows():
        tickets.append({**ticket("正式（未验证）·单关", [l], FORMAL_EV), "tier": "formal"})
    multi = formal[~formal["single"]]
    if len(multi) >= 2:
        tickets.append({**ticket("正式（未验证）·2串1", [multi.iloc[0], multi.iloc[1]], FORMAL_EV), "tier": "formal"})
    tickets = tickets[:2]  # 正式票最多两张，给娱乐串关留预算

    def legs_in(sp_range):  # pool 已按 EV 降序：每场取 EV 最高的一项，整体也按 EV 排
        return pool[(pool["ev"] >= FUN_MIN_EV) & pool["sp"].between(*sp_range)].drop_duplicates("match_id")
    stable, big = legs_in(FUN_SP), legs_in(BIG_SP)
    by_n = {}
    for n in FUN_PARLAYS:
        src, tag = (big, "大奖") if n >= BIG_PARLAY_MIN_N else (stable, "稳")
        if len(src) >= n:
            by_n[n] = {**ticket(f"娱乐·{n}串1（{tag}）", [src.iloc[i] for i in range(n)], FUN_MIN_EV), "tier": "fun"}
    spent = sum(t["stake"] for t in tickets)
    keep = []
    for n in FUN_PRIORITY:
        if n in by_n and spent + STAKE <= DAILY_CAP:
            keep.append(n)
            spent += STAKE
    return tickets + [by_n[n] for n in sorted(keep)]


def to_markdown(rec):
    L = [f"# 每日推荐 {rec['date']}\n\n",
         f"> 生成于 {rec['generated_at']}。体彩快照 {rec['inputs']['sporttery_fetched_at']}，国际赔率快照 {rec['inputs']['odds_fetched_at']}"
         f"（相差 {rec['inputs']['gap_minutes']} 分钟）。重点联赛在售 {rec['coverage']['selling_focus']} 场，匹配上 {rec['coverage']['matched']} 场。\n"
         "> 只做研究和模拟记录，不构成投注建议。sha256 见同名 .sha256 文件。\n\n"]
    if not rec["tickets"]:
        L.append("今天没有合格的推荐。\n\n")
    total = sum(t["stake"] for t in rec["tickets"])
    for t in rec["tickets"]:
        L.append(f"## {t['type']}（{t['stake']} 元）\n\n")
        for l in t["legs"]:
            L.append(f"- {l['num']} {l['league']} {l['match']}（{l['kickoff']}）：**{l['pool']} {l['pick']}** SP {l['sp']:.2f}，"
                     f"市场概率 {l['p']:.1%}，期望回报 {l['ev']:+.1%}，**低于 {l['min_sp']:.2f} 就别买**\n")
        pw = f"{t['p_win']:.1%}" if t["p_win"] >= 0.01 else f"约 {1 / t['p_win']:,.0f} 分之一"
        L.append(f"\n合计赔率 {t['odds']:,.2f}，中奖可拿 {t['payout']:,.2f} 元，中奖概率 {pw}，期望回报 {t['ev']:+.1%}。\n\n")
    if rec["tickets"]:
        L.append(f"今日合计 {total} 元。\n\n")
    legs = pd.DataFrame(rec["legs"])
    if len(legs):
        top = legs.sort_values("ev", ascending=False).head(8)
        L.append("## 期望回报最高的选项\n\n| 场次 | 比赛 | 玩法 | 选项 | SP | 市场概率 | 期望回报 | 可单关 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for l in top.itertuples():
            L.append(f"| {l.num} | {l.home} vs {l.away} | {l.pool} | {l.pick} | {l.sp:.2f} | {l.p:.1%} | {l.ev:+.1%} | {'是' if l.single else '否'} |\n")
        L.append("\n> 「正式（未验证）」：按国际市场算期望回报 ≥ 5%，历史回测还不能证明真能赚，等实盘记账检验。"
                 "娱乐串关按期望回报从高到低取腿：2/3/4 串 1 用赔率 1.5–3 的稳腿，6/8 串 1 用赔率 2.2–4 的腿拉大奖金，长期仍是小亏，图个乐。让球概率由比分模型从国际市场胜平负概率推算（第 3 步已验证）。\n")
    if rec["coverage"]["unmatched"]:
        L.append("\n未匹配上国际赔率的场次：" + "、".join(f"{u['num']} {u['home']}vs{u['away']}" for u in rec["coverage"]["unmatched"]) + "\n")
    return "".join(L)


def main():
    rec = build(write_alias="--force-print" not in sys.argv)
    OUT.mkdir(exist_ok=True)
    jf, mf, hf = (OUT / f"{rec['date']}{ext}" for ext in (".json", ".md", ".sha256"))
    if jf.exists() and "--force-print" not in sys.argv:
        print(f"{jf.name} 已存在，不覆盖（推荐一旦生成即冻结）")
        return
    age = (datetime.now(timezone.utc) - pd.Timestamp(rec["inputs"]["odds_fetched_at"])).total_seconds() / 60
    if age > 120 and "--force-print" not in sys.argv:
        print(f"国际赔率快照已是 {age:.0f} 分钟前的，价格可能过期，不生成推荐")
        return
    if rec["inputs"]["gap_minutes"] > 90:
        print(f"两份快照相差 {rec['inputs']['gap_minutes']} 分钟，不生成正式推荐")
        if "--force-print" not in sys.argv:
            return
    body = json.dumps(rec, ensure_ascii=False, indent=1, default=str)
    if "--force-print" in sys.argv:
        print(to_markdown(rec))
        return
    jf.write_text(body)
    mf.write_text(to_markdown(rec))
    hf.write_text(hashlib.sha256(body.encode()).hexdigest() + f"  {jf.name}\n")
    print(f"wrote {jf.name}")


if __name__ == "__main__":
    main()
