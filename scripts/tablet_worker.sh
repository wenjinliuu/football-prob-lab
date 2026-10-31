#!/data/data/com.termux/files/usr/bin/bash
# 小米平板（Termux）上的变赔历史回补：按 data/backfill/shard_tablet.txt 分批拉取，每 200 场提交并推送一次。
# 已拉过的场次自动跳过，可以随时中断后重跑。任何请求异常或仓库里出现 data/backfill/HALT 都会停止。
# 国内连 GitHub 不稳定：同步/上传失败不影响采集（采集只连体彩），数据先存在平板上，之后自动重试上传。
set -u
cd "$(dirname "$0")/.."
SHARD=data/backfill/shard_tablet.txt
OUT=data/raw/sporttery/history
CHUNK=200
git config user.name "tablet-worker"
git config user.email "tablet-worker@users.noreply.github.com"

sync() {  # 拉取远端（用于检查 HALT），失败不致命
  for i in 1 2 3; do
    timeout 90 git pull -q --rebase origin main && return 0
    echo "  连 GitHub 失败（第 $i 次），10 秒后重试…"; sleep 10
  done
  echo "  GitHub 暂时连不上，先继续采集，稍后再上传"; return 1
}

upload() {  # 推送本地提交，失败不致命
  for i in 1 2 3; do
    timeout 120 git pull -q --rebase origin main && timeout 120 git push -q origin main && { echo "  已上传"; return 0; }
    echo "  上传失败（第 $i 次），10 秒后重试…"; sleep 10
  done
  echo "  上传暂时失败，数据已保存在平板上，下一批再试"; return 1
}

total=$(wc -l < "$SHARD")
for ((start=1; start<=total; start+=CHUNK)); do
  echo "== 第 $(( (start-1)/CHUNK + 1 )) 批（共 $(( (total+CHUNK-1)/CHUNK )) 批），同步仓库中…"
  sync && [ -f data/backfill/HALT ] && { echo "发现 HALT 标记，停止"; exit 3; }
  sed -n "${start},$((start+CHUNK-1))p" "$SHARD" > "$TMPDIR/chunk.txt"
  python3 scripts/fetch_history.py --shard "$TMPDIR/chunk.txt" --out "$OUT"
  rc=$?
  git add "$OUT"
  if ! git diff --cached --quiet; then
    git commit -q -m "data: sporttery history (tablet) $(date +%F_%H%M)"
  fi
  echo "提交并上传中…"
  upload
  if [ $rc -ne 0 ]; then echo "采集停止（代码 $rc），已拉到的部分已保存"; exit $rc; fi
  echo "进度：$((start+CHUNK-1 < total ? start+CHUNK-1 : total)) / $total"
done

echo "采集全部完成，确保上传成功…"
for i in $(seq 1 30); do
  [ -z "$(git log origin/main..HEAD --oneline 2>/dev/null)" ] && { echo "平板这一份全部完成"; exit 0; }
  upload && continue
  sleep 60
done
echo "还有数据没上传成功：网络好的时候在 football-prob-lab 目录里运行 git pull --rebase origin main && git push origin main"
