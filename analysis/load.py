"""把 data/raw/sporttery 下的原始 JSON 整理成 DataFrame。

- matches(): 赛果接口，每场一行（同一 matchId 取最后抓取的一份）。
- odds_history(): 变赔接口，每次变赔一行（HAD / HHAD）。
只读原始文件，不修改。
"""
import json
import re
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "sporttery"
SCORE = re.compile(r"^(\d+):(\d+)$")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _outcome(diff):
    return "H" if diff > 0 else "D" if diff == 0 else "A"


def matches() -> pd.DataFrame:
    rows = {}
    for f in sorted((RAW / "results").glob("*.json"), key=lambda p: p.name.split("__")[-1]):
        for m in json.loads(f.read_text())["data"]["value"]["matchResult"]:
            rows[m["matchId"]] = m  # 按抓取时间排序，后抓的覆盖先抓的
    out = []
    for m in rows.values():
        s = SCORE.match(m["sectionsNo999"] or "")
        gl = _num(m["goalLine"])
        hg, ag = (int(s.group(1)), int(s.group(2))) if s else (None, None)
        out.append({
            "match_id": int(m["matchId"]),
            "date": m["matchDate"],
            "league_id": int(m["leagueId"]),
            "league": m["leagueNameAbbr"],
            "home": m["homeTeam"],
            "away": m["awayTeam"],
            "home_goals": hg,
            "away_goals": ag,
            "pool_status": m["poolStatus"],
            "single": int(m.get("bettingSingle") or 0),
            "had_h": _num(m["h"]),
            "had_d": _num(m["d"]),
            "had_a": _num(m["a"]),
            "goal_line": gl,
            "had_result": _outcome(hg - ag) if s else None,
            "hhad_result": _outcome(hg + gl - ag) if s and gl == gl else None,
        })
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    return df.sort_values(["date", "match_id"]).reset_index(drop=True)


def odds_history() -> pd.DataFrame:
    """每次变赔一行。seq=0 为初赔，is_final 标记终赔（接口按时间倒序返回）。"""
    latest = {}
    hdir = RAW / "history"
    for f in sorted(hdir.glob("*.json"), key=lambda p: p.name.split("__")[-1]) if hdir.exists() else []:
        it = json.loads(f.read_text())
        if it.get("status") == 200 and (it.get("data") or {}).get("errorCode") == "0":
            latest[it["matchId"]] = it  # 同一场抓过多次时取最后一次
    out = []
    for it in latest.values():
        v = it["data"]["value"]
        for pool, key in (("had", "hadList"), ("hhad", "hhadList")):
            lst = list(reversed(v.get(key) or []))  # 改成时间正序
            for i, o in enumerate(lst):
                out.append({
                    "match_id": int(it["matchId"]),
                    "pool": pool,
                    "seq": i,
                    "is_final": i == len(lst) - 1,
                    "ts": pd.to_datetime(f'{o["updateDate"]} {o["updateTime"]}'),
                    "h": _num(o["h"]), "d": _num(o["d"]), "a": _num(o["a"]),
                    "goal_line": _num(o.get("goalLine")),
                })
    cols = ["match_id", "pool", "seq", "is_final", "ts", "h", "d", "a", "goal_line"]
    return pd.DataFrame(out, columns=cols)
