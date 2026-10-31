#!/data/data/com.termux/files/usr/bin/bash
# 小米平板（Termux）上的变赔历史回补：按 data/backfill/shard_tablet.txt 分批拉取，每 200 场提交并推送一次。
# 已拉过的场次自动跳过，可以随时中断后重跑。任何请求异常或仓库里出现 data/backfill/HALT 都会停止。
set -u
cd "$(dirname "$0")/.."
SHARD=data/backfill/shard_tablet.txt
OUT=data/raw/sporttery/history
CHUNK=200
git config user.name "tablet-worker"
git config user.email "tablet-worker@users.noreply.github.com"

total=$(wc -l < "$SHARD")
for ((start=1; start<=total; start+=CHUNK)); do
  git pull -q --rebase origin main || { echo "git pull 失败，稍后重跑"; exit 1; }
  if [ -f data/backfill/HALT ]; then echo "发现 HALT 标记，停止"; exit 3; fi
  sed -n "${start},$((start+CHUNK-1))p" "$SHARD" > "$TMPDIR/chunk.txt"
  python3 scripts/fetch_history.py --shard "$TMPDIR/chunk.txt" --out "$OUT"
  rc=$?
  git add "$OUT"
  if ! git diff --cached --quiet; then
    git commit -q -m "data: sporttery history (tablet) $(date +%F_%H%M)"
    for i in 1 2 3; do git pull -q --rebase origin main && git push -q origin main && break; sleep 5; done
  fi
  if [ $rc -ne 0 ]; then echo "采集停止（代码 $rc），已提交已拉到的部分"; exit $rc; fi
  echo "进度：$((start+CHUNK-1 < total ? start+CHUNK-1 : total)) / $total"
done
echo "平板这一份全部完成"
