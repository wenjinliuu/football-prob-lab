// 每日采集云函数（定时触发，腾讯云开发上海，国内出口）。用户 2026-11-01 提议并同意新增定时器。
// 1. 体彩：当日在售 SP（calc）+ 前一天赛果（results），低频，遇非 200 立即停止，不重试、不绕过。
// 2. 国际赔率：The Odds API（h2h，eu 区，五大联赛 + 欧冠/欧罗巴/欧协联），每次 8 点额度。
// 3. 把原始响应一次性提交到 GitHub 仓库（只新增文件，路径与 scripts/collect.mjs、scripts/odds.mjs 一致），
//    提交会触发 GitHub 上的 daily-picks 生成推荐。
// 若当天推荐已经存在（主定时器已成功），备用定时器直接跳过。
// 环境变量：ODDS_API_KEY、GH_DATA_TOKEN（由 CI 从 GitHub Secrets 注入，不写入代码和日志）。
const REPO = 'wenjinliuu/football-prob-lab';
const SPORTTERY = 'https://webapi.sporttery.cn/gateway';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36';
const SPORTS = [
  'soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_france_ligue_one',
  'soccer_uefa_champs_league', 'soccer_uefa_europa_league', 'soccer_uefa_europa_conference_league',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const bjDate = (offsetDays = 0) => new Date(Date.now() + 8 * 3600e3 + offsetDays * 86400e3).toISOString().slice(0, 10);

async function sporttery(path, referer) {
  const url = `${SPORTTERY}${path}`;
  const t0 = Date.now();
  const rec = { url, fetchedAt: new Date().toISOString() };
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': UA, Referer: referer, Accept: 'application/json, text/plain, */*', 'Accept-Language': 'zh-CN,zh;q=0.9' },
      signal: AbortSignal.timeout(15000),
    });
    const body = await res.text();
    rec.status = res.status;
    try { rec.data = JSON.parse(body); } catch (_) { rec.data = null; rec.snippet = body.slice(0, 300); }
  } catch (e) {
    rec.status = 'error'; rec.error = String(e); rec.data = null;
  }
  rec.ms = Date.now() - t0;
  return rec;
}
const ok = (r) => r.status === 200 && r.data && r.data.errorCode === '0';

async function gh(method, path, body) {
  for (let i = 0; i < 4; i++) {
    try {
      const res = await fetch(`https://api.github.com/repos/${REPO}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${process.env.GH_DATA_TOKEN}`, 'User-Agent': 'football-prob-lab-cloud',
          Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(30000),
      });
      const text = await res.text();
      return { status: res.status, json: text ? JSON.parse(text) : null };
    } catch (e) {
      console.log(`github ${method} ${path} network error (try ${i + 1}): ${String(e).slice(0, 120)}`);
      await sleep(3000 * (i + 1));
    }
  }
  return { status: 'error', json: null };
}

async function commitFiles(files, message) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const ref = await gh('GET', '/git/ref/heads/main');
    if (ref.status !== 200) { console.log(`get ref failed: ${ref.status}`); continue; }
    const head = ref.json.object.sha;
    const commit = await gh('GET', `/git/commits/${head}`);
    const tree = await gh('POST', '/git/trees', {
      base_tree: commit.json.tree.sha,
      tree: files.map((f) => ({ path: f.path, mode: '100644', type: 'blob', content: f.content })),
    });
    if (tree.status !== 201) { console.log(`create tree failed: ${tree.status}`); continue; }
    const c = await gh('POST', '/git/commits', { message, tree: tree.json.sha, parents: [head] });
    if (c.status !== 201) { console.log(`create commit failed: ${c.status}`); continue; }
    const up = await gh('PATCH', '/git/refs/heads/main', { sha: c.json.sha });
    if (up.status === 200) return c.json.sha;
    console.log(`update ref failed: ${up.status}（可能有并发提交，重试）`);
    await sleep(2000);
  }
  return null;
}

exports.main = async (event) => {
  const trigger = (event && event.TriggerName) || 'manual';
  const today = bjDate(0);
  const yesterday = bjDate(-1);
  if (!process.env.GH_DATA_TOKEN || !process.env.ODDS_API_KEY) {
    console.log('缺少 GH_DATA_TOKEN 或 ODDS_API_KEY，跳过');
    return { skipped: 'missing secrets' };
  }
  const exists = await gh('GET', `/contents/recommendations/${today}.md`);
  if (exists.status === 200) {
    console.log(`${today} 的推荐已存在（${trigger}），跳过`);
    return { skipped: 'already done' };
  }

  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const files = [];
  const summary = [];
  const add = (path, obj) => files.push({ path, content: JSON.stringify(obj) });

  // 1. 体彩在售 + 昨天赛果（任何异常即停止体彩部分）
  const calc = await sporttery('/jc/football/getMatchCalculatorV1.qry?poolCode=had,hhad&channel=c', 'https://www.sporttery.cn/');
  add(`data/raw/sporttery/calc/calc__${stamp}.json`, calc);
  summary.push(`calc ${calc.status}`);
  if (ok(calc)) {
    let page = 1, pages = 1;
    do {
      await sleep(2000);
      const qs = new URLSearchParams({ matchBeginDate: yesterday, matchEndDate: yesterday, leagueId: '', pageSize: '100', pageNo: String(page), isFix: '0', matchPage: '1', pcOrWap: '1' });
      const r = await sporttery(`/uniform/football/getUniformMatchResultV1.qry?${qs}`, 'https://www.lottery.gov.cn/jc/zqsgkj/');
      add(`data/raw/sporttery/results/${yesterday}_${yesterday}_p${page}__${stamp}.json`, r);
      summary.push(`results p${page} ${r.status}`);
      if (!ok(r)) break;
      pages = Number(r.data.value.pages) || 1;
      page += 1;
    } while (page <= pages);
  }

  // 2. 国际赔率（URL 里有 Key，不记录 URL）
  for (const sport of SPORTS) {
    const fetchedAt = new Date().toISOString();
    let rec;
    try {
      const qs = new URLSearchParams({ apiKey: process.env.ODDS_API_KEY, regions: 'eu', markets: 'h2h', oddsFormat: 'decimal', dateFormat: 'iso' });
      const res = await fetch(`https://api.the-odds-api.com/v4/sports/${sport}/odds?${qs}`, { signal: AbortSignal.timeout(20000) });
      const text = await res.text();
      let data = null;
      try { data = JSON.parse(text); } catch (_) { /* 非 JSON */ }
      rec = { sport, status: res.status, fetchedAt, requestsUsed: res.headers.get('x-requests-used'), requestsRemaining: res.headers.get('x-requests-remaining'), data };
    } catch (e) {
      rec = { sport, status: 'error', fetchedAt, error: String(e).slice(0, 200), data: null };
    }
    add(`data/raw/odds_api/h2h/${sport}__${stamp}.json`, rec);
    summary.push(`${sport} ${rec.status}`);
    if (rec.status !== 200) break;
    await sleep(1000);
  }

  // 3. 提交到 GitHub
  const sha = await commitFiles(files, `data: cloud daily ${today} (${trigger})`);
  summary.push(sha ? `committed ${sha.slice(0, 7)} (${files.length} files)` : 'COMMIT FAILED');
  console.log(summary.join(' | '));
  return { trigger, summary };
};
