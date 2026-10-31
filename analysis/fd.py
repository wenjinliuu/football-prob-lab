"""Football-Data.co.uk 历史赔率（含 Pinnacle 赛前价和收盘价）。

条款仅限个人使用：CSV 只缓存在本地 data/external/（已 gitignore），不提交、不再分发。
"""
import io
import time
import urllib.request
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "external" / "football-data"
URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
# 体彩 leagueId → Football-Data 联赛代码
LEAGUES = {25: "E0", 62: "SP1", 40: "I1", 37: "D1", 32: "F1"}
FIRST_SEASON = 2017  # 2017/18 赛季
COLS = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
        "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA", "AvgH", "AvgD", "AvgA", "AvgCH", "AvgCD", "AvgCA"]


def seasons(last_year):
    """2017 → '1718' ... 直到 last_year 开始的赛季。"""
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(FIRST_SEASON, last_year + 1)]


def _get(season, div, refresh):
    f = CACHE / f"{season}_{div}.csv"
    if f.exists() and not refresh:
        return f.read_bytes()
    req = urllib.request.Request(URL.format(season=season, div=div), headers={"User-Agent": "football-prob-lab (personal research)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    time.sleep(1)  # 礼貌间隔
    return data


def load(last_year, refresh_current=True):
    """下载（或读缓存）所有赛季、五大联赛，返回统一列的 DataFrame。当前赛季每次重新下载。"""
    frames = []
    ss = seasons(last_year)
    for season in ss:
        for lid, div in LEAGUES.items():
            try:
                raw = _get(season, div, refresh=refresh_current and season == ss[-1])
            except Exception as e:  # 当前赛季文件可能还没有
                print(f"skip {season} {div}: {e}")
                continue
            df = pd.read_csv(io.BytesIO(raw.removeprefix(b"\xef\xbb\xbf")), encoding="latin-1")
            df.columns = [c.strip() for c in df.columns]
            df = df.dropna(subset=["HomeTeam", "AwayTeam"]).reindex(columns=COLS)
            df["Div"] = div
            df["season"] = season
            df["league_id"] = lid
            frames.append(df)
    fd = pd.concat(frames, ignore_index=True)
    fd["date"] = pd.to_datetime(fd["Date"], dayfirst=True, format="mixed")
    for c in COLS[5:]:
        fd[c] = pd.to_numeric(fd[c], errors="coerce")
    fd["HomeTeam"] = fd["HomeTeam"].str.strip()
    fd["AwayTeam"] = fd["AwayTeam"].str.strip()
    return fd.reset_index(drop=True)
