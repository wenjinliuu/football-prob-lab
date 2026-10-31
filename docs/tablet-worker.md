# 小米平板：变赔历史回补（线 3）

只做一次性回补，跑完就可以卸掉。每场间隔 1.5 秒、单线程，遇到异常自动停止。负责 `data/backfill/shard_tablet.txt` 里的 3156 场，预计 2.5 小时左右。

## 1. 安装 Termux

从 **F-Droid** 安装 Termux（应用商店里的旧版无法更新软件包）：浏览器打开 f-droid.org，搜索 Termux 下载安装。

小米系统设置里：**应用设置 → Termux → 省电策略选「无限制」**，并允许后台运行，否则锁屏后会被系统杀掉。

## 2. 在 GitHub 生成一个只能写本仓库的 Token

手机浏览器打开 github.com → 右上角头像 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token：

- Expiration：7 天
- Repository access：Only select repositories → `football-prob-lab`
- Permissions → Repository permissions → **Contents：Read and write**（其他都不用开）

生成后复制这串 Token。**不要发到聊天里**，只粘贴进 Termux。

## 3. 在 Termux 里运行

打开 Termux，逐行粘贴：

```bash
pkg update -y && pkg install -y python git
termux-wake-lock
read -s -p "粘贴 Token 后回车：" T; echo
git clone --depth 1 "https://x-access-token:$T@github.com/wenjinliuu/football-prob-lab.git"
cd football-prob-lab && bash scripts/tablet_worker.sh
```

屏幕上会显示进度。中途断了，重新打开 Termux 执行：

```bash
cd football-prob-lab && termux-wake-lock && bash scripts/tablet_worker.sh
```

## 4. 跑完以后

- 显示「平板这一份全部完成」即可关掉。
- 去 GitHub 删除刚才那个 Token（同一页面 → Delete），再在 Termux 里 `rm -rf football-prob-lab`。
