# football-prob-lab 足球概率实验室

个人研究项目：用国际市场赔率和球队实力模型估计足球比赛概率，对比中国体彩竞彩 SP，做严格的按时间顺序回测，并每天给出小额娱乐性建议。**不接入任何投注或购彩功能。**

研究报告：<https://claude.ai/artifact/QWaNmjcMtA9DPKoJcLqdAz>

## 当前进度

- [x] 竞彩数据采集云函数 `jc-collector`（腾讯云开发，HTTP 函数）
- [x] 部署工作流（复用 `wenjinliuu/cloudbase-mcp-worker` 的 CloudBase CLI 部署）
- [x] 每日采集工作流：北京时间 10:17 拉当日在售 SP 与前一天赛果
- [ ] 历史回补（2018 年起，官方赛果 + 变赔历史）
- [ ] 国际赔率（The Odds API / Football-Data）与赛事匹配
- [ ] 模型 A 纯赔率基准、模型 B Elo + Dixon-Coles、每日建议与前瞻账本

## 架构

```text
GitHub Actions ──tcb fn invoke──▶ 腾讯云开发 jc-collector（国内出口）──▶ 体彩接口
      │                                   （GitHub 服务器直连会被拦截：HTTP 567）
      └─▶ 原始 JSON 只新增不覆盖 ─▶ 解析 / 回测 / 每日建议（后续）
```

## 需要配置的密钥

在本仓库 Settings → Secrets and variables → Actions 新增：

| 名称 | 必需 | 来源 |
| --- | --- | --- |
| `CLOUDBASE_API_KEY` | 是 | 云开发控制台 → 环境 `wenjin-cloudbase` → API Key。建议新建一把专用的，命名 `gh-football-prob-lab` |

详细说明见 [cloudbase-mcp-worker/docs/cloudbase-cli.md](https://github.com/wenjinliuu/cloudbase-mcp-worker/blob/main/docs/cloudbase-cli.md)。

## 数据与合规

- 体彩数据归体彩中心所有。本仓库是**公开仓库**时，采集结果只作为 Actions 附件保存 30 天，不提交进仓库；改为 Private 后才会把原始数据提交存档。
- 采集只用普通浏览器请求头、低频；云函数遇到任何非 200 响应立即停止，不重试、不换 IP、不绕过访问控制。
- 原始响应只新增、不覆盖，所有派生数据都可以从原始数据重建，回测只使用赛前时间戳之前的数据。

## 手动运行采集

Actions → Collect sporttery data → Run workflow，`tasks` 示例：

```text
calc
results:2026-10-01:2026-10-07
history:2042385,2042386
```
