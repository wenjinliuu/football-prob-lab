"""把体彩比赛和 Football-Data 的比赛对上。

1. 队名对照：体彩每支队有固定 teamId。在同联赛、日期相差 ≤1 天且比分相同的比赛里，
   统计「体彩主队 ID ↔ FD 主队名」「体彩客队 ID ↔ FD 客队名」共同出现次数，取最多的那个。
   真正的对应关系每赛季出现几十次，偶然撞日期的噪声只有零星几次。
2. 逐场匹配：同联赛、主客队都对上、日期相差 ≤2 天，且比分一致。
对照表写到 data/mappings/team_alias_fd.csv（我们自己的派生数据，可以提交），
置信度低的标记出来人工复核。
"""
from pathlib import Path

import pandas as pd

import fd as fdmod
import load

MAP = Path(__file__).resolve().parent.parent / "data" / "mappings" / "team_alias_fd.csv"


def build_alias(st, fd):
    pairs = []
    for lid in fdmod.LEAGUES:
        a = st[st["league_id"] == lid][["match_id", "date", "home_id", "away_id", "home_goals", "away_goals"]]
        b = fd[fd["league_id"] == lid][["date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]]
        for shift in (-1, 0, 1):
            x = a.assign(key=a["date"] + pd.Timedelta(days=shift)).merge(
                b, left_on=["key", "home_goals", "away_goals"], right_on=["date", "FTHG", "FTAG"], suffixes=("", "_fd"))
            pairs.append(pd.DataFrame({"team_id": x["home_id"], "fd": x["HomeTeam"], "lid": lid}))
            pairs.append(pd.DataFrame({"team_id": x["away_id"], "fd": x["AwayTeam"], "lid": lid}))
    p = pd.concat(pairs).groupby(["team_id", "fd"]).size().rename("n").reset_index()
    tot = p.groupby("team_id")["n"].transform("sum")
    p = p.assign(share=p["n"] / tot).sort_values(["team_id", "n"], ascending=[True, False])
    best = p.groupby("team_id").head(2).copy()
    best["rank"] = best.groupby("team_id").cumcount()
    top = best[best["rank"] == 0].set_index("team_id")
    second = best[best["rank"] == 1].set_index("team_id")["n"].reindex(top.index).fillna(0)
    top = top.assign(n2=second, ratio=top["n"] / second.clip(lower=1))
    names = pd.concat([st[["home_id", "home"]].set_axis(["team_id", "cn"], axis=1),
                       st[["away_id", "away"]].set_axis(["team_id", "cn"], axis=1)])
    cn = names.groupby("team_id")["cn"].agg(lambda s: s.value_counts().index[0])
    out = top.join(cn).reset_index()[["team_id", "cn", "fd", "n", "n2", "ratio"]]
    # 第一名次数至少是第二名的 3 倍，且总次数 ≥ 6，视为可靠
    out["status"] = ["ok" if (r >= 3 and n >= 6) else "review" for r, n in zip(out["ratio"], out["n"])]
    return out.sort_values("cn").reset_index(drop=True)


def match(st, fd, alias):
    m = alias[alias["status"] == "ok"].set_index("team_id")["fd"]
    s = st.assign(fd_home=st["home_id"].map(m), fd_away=st["away_id"].map(m)).dropna(subset=["fd_home", "fd_away"])
    x = s.merge(fd, left_on=["league_id", "fd_home", "fd_away"], right_on=["league_id", "HomeTeam", "AwayTeam"], suffixes=("", "_fd"))
    x = x[(x["date_fd"] - x["date"]).abs() <= pd.Timedelta(days=2)]
    x["score_ok"] = (x["home_goals"] == x["FTHG"]) & (x["away_goals"] == x["FTAG"])
    return x


def run(write=True):
    st = load.matches()
    st = st[st["league_id"].isin(fdmod.LEAGUES) & st["home_goals"].notna()]
    fd = fdmod.load(int(st["year"].max()))
    alias = build_alias(st, fd)
    if write:
        MAP.parent.mkdir(parents=True, exist_ok=True)
        alias.to_csv(MAP, index=False)
    x = match(st, fd, alias)
    return st, fd, alias, x


if __name__ == "__main__":
    st, fd, alias, x = run()
    print(f"teams: {len(alias)}  review: {(alias.status == 'review').sum()}")
    print(alias[alias.status == "review"].to_string())
    print(f"sporttery focus matches: {len(st)}  matched: {x.match_id.nunique()}  dup: {x.match_id.duplicated().sum()}  score mismatch: {(~x.score_ok).sum()}")
    print(x.groupby(["year", "league"]).size().unstack())
