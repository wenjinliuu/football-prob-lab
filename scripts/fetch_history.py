#!/usr/bin/env python3
"""体彩变赔历史采集（独立版，只用 Python 标准库）。供电子衣橱服务器和小米平板使用，规则与 jc-collector 云函数一致：
- 普通浏览器请求头，每场间隔 1.5 秒，单线程；
- 遇到非 200、非 JSON、超时、errorCode 非 '0'（延期/取消场次的空数据除外）立即停止，不重试；
- 每场输出一行 JSON（与云函数返回的单场结构相同）。

用法：
  python3 fetch_history.py ID [ID ...]             # 结果逐行打印到标准输出
  python3 fetch_history.py --shard 文件 --out 目录  # 读名单，跳过目录里已有的场次，每场存一个文件
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getOddsHistoryV1.qry?matchId={}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36",
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
GAP = 1.5


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetch(mid):
    url = URL.format(mid)
    t0 = time.time()
    rec = {"matchId": int(mid), "url": url, "fetchedAt": now()}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
            rec["status"] = r.status
    except urllib.error.HTTPError as e:
        rec["status"], body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # 超时、断网等
        rec.update(status="error", error=str(e), ms=int((time.time() - t0) * 1000), data=None)
        return rec
    rec["ms"] = int((time.time() - t0) * 1000)
    try:
        rec["data"] = json.loads(body)
    except ValueError:
        rec["data"], rec["snippet"] = None, body[:300]
    return rec


def ok(rec):
    d = rec.get("data") or {}
    if rec.get("status") != 200:
        return False
    return d.get("errorCode") == "0" or (d.get("success") is False and d.get("errorCode") == "")


def ok_file(f):
    try:
        return ok(json.loads(f.read_text()))
    except ValueError:
        return False


def done_ids(out):
    s = set()
    for f in out.glob("*.json"):
        try:
            if ok(json.loads(f.read_text())):
                s.add(f.name.split("__")[0])
        except ValueError:
            pass
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--shard")
    ap.add_argument("--out")
    ap.add_argument("--gap", type=float, default=GAP, help="每场间隔秒数，默认 1.5，最小 1.5")
    ap.add_argument("--reverse", action="store_true", help="从名单末尾往前拉（同一台机器上第二条线用，与正序的线在中间会合）")
    ap.add_argument("--halt-url", help="每 20 场检查一次，返回 200 就停止（三条线共用的停止标记）")
    a = ap.parse_args()
    ids = [x.strip() for x in (Path(a.shard).read_text().split() if a.shard else a.ids) if x.strip()]
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        skip = done_ids(out)
        ids = [i for i in ids if i not in skip]
    if a.reverse:
        ids.reverse()
    print(f"[fetch_history] {len(ids)} to fetch", file=sys.stderr)
    for n, mid in enumerate(ids):
        if a.halt_url and n % 20 == 0:
            try:
                with urllib.request.urlopen(a.halt_url, timeout=10) as r:
                    if r.status == 200:
                        print("[fetch_history] HALT 标记存在，停止", file=sys.stderr)
                        return 3
            except Exception:
                pass
        if out and any(ok_file(f) for f in out.glob(f"{mid}__*.json")):
            continue  # 另一条线已经拉过（两条线会合时）
        rec = fetch(mid)
        line = json.dumps(rec, ensure_ascii=False)
        if out:
            stamp = rec["fetchedAt"].replace(":", "-").replace(".", "-")
            (out / f"{mid}__{stamp}.json").write_text(line)
        else:
            print(line, flush=True)
        if not ok(rec):
            print(f"[fetch_history] 异常，停止：matchId={mid} status={rec.get('status')} {rec.get('snippet') or rec.get('error') or ''}", file=sys.stderr)
            return 1
        print(f"[{n + 1}/{len(ids)}] {mid} ok {rec.get('ms', 0) / 1000:.1f}s", file=sys.stderr, flush=True)
        time.sleep(max(GAP, a.gap))
    print("[fetch_history] 完成", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
