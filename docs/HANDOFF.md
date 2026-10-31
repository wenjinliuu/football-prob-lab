# 交接文档：足球概率实验室（football-prob-lab）

> 2026-10-31 由 claude.ai 会话交接给 Claude Code。新会话先读本文件和根目录 `AGENTS.md`。
> 研究报告（含数据源、开源项目审查、模型和回测方案）：<https://claude.ai/artifact/QWaNmjcMtA9DPKoJcLqdAz>

## 1. 项目是什么

用户 wenjin（GitHub `wenjinliuu`）的个人研究项目，**图一乐、顺便学习**：

- 研究中国体彩**竞彩足球**的胜平负（HAD）和让球胜平负（HHAD）。
- 每天自动拉数据、自动回测，并给出**每天几块钱**的小额娱乐建议。网页可视化是次要的。
- **不做自动投注、不接入任何购彩功能。**
- 用户不懂建模，期望 AI 主导设计，用中文沟通（代码和命令可以是英文）。

## 2. 已经确定的结论（不要推翻，除非有新数据）

1. **用户原先的"中间赔率"策略长期亏损。** 用 lottery-football 仓库的约 3 万场体彩 SP（来源未完全核实）回测：
   - 胜平负中间赔率：命中 26.0%，隐含概率 26.3%，ROI -13.0%（95% 区间 -14.7% ~ -11.4%），2014–2026 每年都为负。
   - 让球中间赔率：命中 27.4%，ROI -12.9%；按让球数拆开（-2/-1/+1/+2）全部为负。
   - 体彩 HAD 和 HHAD 的返还率中位数都约 88.6%（抽水约 11%）；Pinnacle 等低水位公司约 97%。
2. **球队实力模型几乎不可能跑赢市场收盘赔率。** wlrwx/football-engine 用 7.2 万场严格前向回测，Dixon-Coles 模型在 21 个联赛全部显著差于去水后的市场概率；学术文献结论一致。
3. **因此设计原则是：国际市场去水概率为主，模型只做"影子"记录**（只记录、不影响建议，等真实前瞻数据证明更好再启用）。
4. 串关的理论返还率是单场返还率的 N 次方（4 串 1 约 62%），建议只做单关，串关最多作为娱乐选项并标明预期亏损。
5. 主要评估指标：样本外 Log Loss / Brier / RPS、校准曲线、CLV（建议时 SP 对比停售 SP，以及对比 Pinnacle 公允价）。ROI 只作参考，并必须给出置信区间。

## 3. 当前已经在运行的东西

### 3.1 架构

```text
GitHub Actions（本仓库）
  ├─ deploy-cloudbase.yml ──调用──▶ wenjinliuu/cloudbase-mcp-worker 的可复用工作流 cloudbase-deploy.yml
  │                                   └─ tcb fn deploy jc-collector → 腾讯云开发
  └─ collect-sporttery.yml ──tcb fn invoke --api-key──▶ jc-collector（HTTP 云函数，上海，国内出口）──▶ 体彩接口
                                                            （GitHub 服务器直连体彩会被拦：HTTP 567）
```

- 云开发环境：`wenjin-cloudbase-d1empq882391ac1`（ap-shanghai，个人版）。同一环境里还有用户的彩票 App 后端函数（lottery-*、calenease-*），**不要动它们**。
- 本仓库 Secret：`CLOUDBASE_API_KEY`（云开发环境级 API Key，用户已配置）。
- 仓库现在是 **public**：私有仓库的 Actions 因为 GitHub 账单问题无法启动，用户已改回公开。

### 3.2 文件

| 路径 | 作用 |
| --- | --- |
| `cloudbase/jc-collector/http-index.js` | HTTP 云函数，POST body `{action}`：`calc`（在售 SP）、`results`（按日期区间查赛果，单次 ≤30 天，分页 100）、`history`（按 matchId 查完整变赔，单次最多 20 场，间隔 1.5 秒）。任一请求非 200 立即停止 |
| `cloudbase/jc-collector/scf_bootstrap` | HTTP 函数启动脚本（监听 9000） |
| `cloudbaserc.json` | 函数配置（type HTTP、Nodejs20.19） |
| `scripts/collect.mjs` | 调用 `tcb fn invoke` 并把原始响应存为带时间戳的 JSON，**只新增不覆盖**。CLI 输出前后有提示文字，脚本截取第 0 列 `{` 到第 0 列 `}` 之间的 JSON |
| `scripts/fetch_history.py` | 独立的变赔历史采集脚本（只用 Python 标准库），规则同云函数；供小米平板 Termux 使用（见 `docs/tablet-worker.md`）。线 2 是电子衣橱的 Lighthouse 服务器：用户把 `ai-wardrobe-lab` 改为公开后，由该仓库的 `sporttery-worker.yml`（仅手动触发）通过 SSH 在服务器 `~/sporttery-worker/` 下运行本脚本，结果存为该仓库的 Actions 附件，再用本仓库 `import-artifacts.yml`（repo=wenjinliuu/ai-wardrobe-lab）转存。衣橱应用代码和容器不动。分工名单 `data/backfill/shard_*.txt`；仓库里出现 `data/backfill/HALT` 时两条线都会停 |
| `.github/workflows/deploy-cloudbase.yml` | 推送 `cloudbase/**` 或手动触发时部署 |
| `analysis/load.py` | 把原始 JSON 整理成 DataFrame：`matches()`（每场一行，含 HAD 终赔、比分、让球、HAD/HHAD 赛果）、`odds_history()`（每次变赔一行，seq=0 初赔、is_final 终赔） |
| `analysis/step1_market.py` | 第 1 步报告：抽水、校准、热门/中间/冷门策略、SP 区间 × 选项 ROI（BH 校正）、初赔 vs 终赔、赔率变动方向 → `reports/step1_market.md` |
| `analysis/fd.py` | 下载 Football-Data 五大联赛 2017/18 起的 CSV（含 Pinnacle 赛前价 PSH 和收盘价 PSCH），缓存在 `data/external/`（gitignore，不提交）。**Pinnacle 只到 2025-12 中旬**，之后只有平均价 |
| `analysis/match_fd.py` | 用体彩 teamId + 同日期同比分的共现次数自动生成队名对照 `data/mappings/team_alias_fd.csv`（status=review 的要人工看），再逐场匹配。2018 年 1771 场匹配 1766 场，比分全一致；没配上的是升降级附加赛 |
| `analysis/step2_pinnacle.py` | 第 2 步报告：抽水对比、体彩 vs Pinnacle 准确度（Shin 去水）、按 Pinnacle 概率算体彩 EV 的价值检验 → `reports/step2_pinnacle.md` |
| `analysis/step3_history.py` | 第 3 步报告（变赔历史 1.4 万场）：让球泊松模型验证、胜平负 vs 让球 EV 对比、开盘/上午/停售三个时点的回报和 CLV → `reports/step3_history.md` |
| `analysis/daily.py` | 每日推荐：最新体彩在售快照 × 最新 Odds API 快照（相差 ≤90 分钟、不超过 2 小时）→ 联赛+开球时间+英文名相似度匹配（中文队→英文名来自 `team_alias_fd.csv`，匹配成功的写进 `team_alias_oddsapi.csv`）→ Pinnacle Shin 去水 → EV。胜平负和让球都参与（让球泊松模型第 3 步验证通过）。正式（未验证）：EV≥5%、SP≤6，可单关出单关，否则组 2串1，最多两张。娱乐串关每天都出：每场取 SP 1.5–3.0、EV≥-8% 里 EV 最高的一项，按 EV 排序取前 n 场组 2/3/4/6/8 串 1（用户 2026-10-31 要求，8 串 1 用稳腿）。每注 2 元、日上限 10 元，超预算时先保留大串。每条腿给底线 SP。输出 `recommendations/YYYY-MM-DD.{json,md,sha256}`，已存在不覆盖。首测 36/36 场匹配正确 |
| `analysis/settle.py` | 赛后结算 → `reports/ledger.md`：推荐票账本、CLV（推荐 SP 对停售终赔）、影子账本（所有 EV≥0 的 HAD 选项各 1 元） |
| `.github/workflows/daily-picks.yml` | 两个采集工作流完成后触发（不是新定时器）：生成当天推荐 + 结算 |
| `.github/workflows/analysis.yml` | 采集成功后或手动触发时重新生成 `reports/` 并提交 |
| `.github/workflows/import-artifacts.yml` | 手动触发：把未过期的 `sporttery-raw-*` 附件补存到 `data/raw/sporttery/`（只新增不覆盖） |
| `scripts/odds.mjs` + `.github/workflows/collect-odds.yml` | The Odds API 国际赔率（h2h、eu 区、五大联赛 + 欧冠/欧罗巴/欧协联），直接在 GitHub Actions 抓，每天北京 10:17（用户 2026-10-31 同意新增此定时任务），存 `data/raw/odds_api/h2h/`。每次 8 点额度，免费档 500 点/月。首次实测 8 个联赛都含 pinnacle |
| `.github/workflows/collect-sporttery.yml` | 每天 02:17 UTC（北京 10:17）拉当日 `calc` 和前一天 `results`；部署成功后自动跑一次；手动触发时可传 `tasks`，例如 `calc results:2026-10-01:2026-10-07 history:2042385,2042386` |

### 3.3 验证记录

- 2026-10-31 部署成功；手动采集成功：`calc` 62 场，`results` 2026-10-30 共 7 场。
- 数据存储：原始 JSON 直接提交到 `data/raw/sporttery/`（用户 2026-10-31 明确决定，仓库公开期间也提交）。更早只存成附件的数据已用 `import-artifacts.yml` 补存。

## 4. 体彩接口要点（已实测）

均为 `https://webapi.sporttery.cn/gateway/...` 下未公开的接口，只带普通浏览器 UA 和 Referer，不需要 Cookie。

- `jc/football/getMatchCalculatorV1.qry?poolCode=had,hhad&channel=c`：在售比赛及 SP。字段在 `value.matchInfoList[].subMatchList[]`，包括 `matchId`、`matchNumStr`、`leagueAbbName`、`homeTeamAbbName`、`homeTeamAbbEnName`、`had{h,d,a,updateTime}`、`hhad{goalLine,h,d,a}`。`goalLine` 为 `-1` 表示主队让 1 球。
- `uniform/football/getUniformMatchResultV1.qry?matchBeginDate=&matchEndDate=&leagueId=&pageSize=100&pageNo=&isFix=0&matchPage=1&pcOrWap=1`（Referer 用 `https://www.lottery.gov.cn/jc/zqsgkj/`）：赛果。`sectionsNo999` 是全场比分，`sectionsNo1` 是半场比分，另有 `goalLine`、最终 HAD 的 `h/d/a`、`winFlag`、`poolStatus`、`value.pages`。2018 年的数据仍然可以查到。
- `uniform/football/getOddsHistoryV1.qry?matchId=`：返回 `hadList`、`hhadList`、`ttgList`、`crsList`、`hafuList`，**按时间倒序**，每条带 `updateDate` / `updateTime`。最后一条（最早）是初赔，第一条（最新）是终赔，终赔与赛果接口中的 h/d/a 一致。
- 合规红线：低频；遇到 403/567 或验证页立即停止；**不用代理池、不解验证码、不绕过 WAF**；用户 2026-10-31 明确决定变赔历史回补三条线并行（见 `data/backfill/`），每条线单线程、间隔 1.5 秒；体彩数据不对外再分发（原始数据提交到本仓库是用户明确决定）。

## 5. CloudBase CLI 注意事项（已审计）

- 登录：`tcb login --cloudbase-api-key "$CLOUDBASE_API_KEY" -e <envId> --yes`。CLI 版本固定为 `@cloudbase/cli@3.8.3`。
- 环境级 API Key **可以**部署、删除函数，也可以用 `--api-key` 调用 **HTTP 函数**；**不能**调用事件函数（会报 `Cam authentication failed`），后者需要腾讯云 SecretId/SecretKey。所以采集函数用 HTTP 类型。
- 托管版 CloudBase MCP（用户自建的 Cloudflare 网关 `cloudbase-hosted-oauth-staging` 转发到腾讯托管 MCP）运行在 cloud mode：可以查询、看日志、删除，但**不能上传函数代码**。部署只走 git → CI。
- 详细说明和审计记录：`wenjinliuu/cloudbase-mcp-worker` 仓库的 `docs/cloudbase-cli.md`。
- 用户的 claude.ai 会话里，用于验证的临时云定时器曾被安全策略拦截（被判定为未授权持久化）。新增定时触发器之前，先征得用户明确同意。

## 6. 下一步（按优先级）

1. **长期存储**：已完成，直接提交到仓库；用户计划额度恢复后把仓库改为私有。
2. **历史回补**（2026-10-31 完成：赛果 2018–2026 全部联赛约 4.9 万场；重点联赛变赔历史 14,001 场）：2018 年起，先做五大联赛加欧冠和欧联。赛果接口按 30 天分段分页，再对每场调用 `history`。整个过程低频：每批 20 场、间隔 1.5 秒，分多天跑完，估计约 2 万场。
3. **规范化**：DuckDB 加 Parquet，表包括 `matches`、`teams/team_aliases`、`sporttery_odds`（每次变赔一行）、`global_odds`、`predictions`（只追加）、`backtests`。详见报告的"数据质量与回测方案"一节。
4. **国际赔率**（历史部分已完成：`analysis/fd.py` + `match_fd.py` + `step2_pinnacle.py`；The Odds API 实时采集已上线，Secret `ODDS_API_KEY` 已配置；和体彩在售场次的实时匹配未做）：Football-Data.co.uk 的 CSV（有 Pinnacle 收盘价，用于历史；条款仅限个人使用，不要再分发），加上 The Odds API 免费档（每天拉一次 eu 区 1X2 和大小球；需要用户注册并新增 `ODDS_API_KEY` Secret）。赛事匹配用开球时间 ±3 小时、联赛、队名别名表，队名别名需要人工审核。
5. **模型 A 回测**（已写好 `analysis/step1_market.py`，等全量数据跑完后看结论）：用官方数据重跑中间、热门、冷门策略，分别按初赔和终赔两种口径，按赔率区间做校准图，并做 BH 多重检验校正。
6. **概率链**：国际赔率去水（Shin 或 power 方法）→ 反解 λ → Dixon-Coles 比分矩阵 → 体彩整数让球的 HAD/HHAD 概率。**不要把亚盘直接当作体彩让球**：体彩让球是三项结果、没有走盘。模型可以用 penaltyblog（MIT）。
7. **每日建议**（已上线 `daily.py` + `settle.py` + `daily-picks.yml`，2026-11-01 起每天自动出单）：先只出单不买，跑 2 周。规则：EV = p × SP − 1 ≥ +5%，并且模型与市场的差 ≤ 0.08 时才正式推荐；没有合格场次时，给出当天 EV 最高的 1–2 个单关作为娱乐选项，写明预期亏损；不推荐 SP > 6 的冷门；单日上限 10 元。建议在开赛前冻结并做哈希，只追加，输了不删。
8. 最后做网页仪表盘（静态页面，可以放 Cloudflare Pages）。

## 7. 参考的外部仓库

- `wenjinliuu/cloudbase-mcp-worker`：MCP 网关、可复用部署工作流 `cloudbase-deploy.yml`、自检工作流 `cloudbase-cli-check.yml`、`docs/cloudbase-cli.md`。
- `wenjinliuu/lottery-data-repo`：用户的彩票开奖仓库，CLI 部署方式最早来自这里。**不要改它。** 里面遗留了一个 `probe/sporttery` 测试分支，已请用户手动删除，删前先确认。
- 借鉴思路（代码许可证要分别确认）：
  - wlrwx/football-engine：前瞻账本、影子模型、CLV、消融实验。无许可证，含绕过 WAF 的代码，不要复制。
  - martineastwood/penaltyblog（MIT）：模型、评级、去水、指标。
  - georgedouzas/sports-betting（MIT）：时间序列回测框架。
  - jwang0127/football-analyze：用同一比分分布统一各玩法。
  - Mboss8/football-prediction-open-record：诚实的前瞻记录写法。
- 现成的竞彩历史 SP 数据集（只用于交叉校验，不作为主数据）：qqyg000/lottery-football 的 `historical_odds_data.csv`（来源不明、无许可证），以及 football-engine 的 `data/historical/odds.csv`。

## 8. 工作约定

- 用中文回复用户。用户只用手机，不开电脑，所有构建和部署都在云端完成。
- 密钥只放在 GitHub Secrets 里，不进入代码、日志或文档。
- 提交信息结尾按要求附上 Co-Authored-By 或会话链接。
