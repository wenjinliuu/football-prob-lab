// 从 The Odds API 拉国际赔率（胜平负 h2h，欧洲区），原始响应存成带时间戳的 JSON（只新增，不覆盖）。
// 用法：ODDS_API_KEY=... node scripts/odds.mjs <outDir>
// 每个联赛 1 次调用、消耗 1 点额度（markets × regions = 1）。Key 只从环境变量读，不写入文件或日志。
import fs from 'node:fs';
import path from 'node:path';

const SPORTS = [
  'soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_france_ligue_one',
  'soccer_uefa_champs_league', 'soccer_uefa_europa_league', 'soccer_uefa_europa_conference_league',
];
const KEY = process.env.ODDS_API_KEY;
const [outDir] = process.argv.slice(2);
if (!outDir || !KEY) { console.error('need outDir and ODDS_API_KEY'); process.exit(2); }

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const dir = path.join(outDir, 'h2h');
fs.mkdirSync(dir, { recursive: true });
const summary = [];
let remaining = null;

for (const sport of SPORTS) {
  const qs = new URLSearchParams({ apiKey: KEY, regions: 'eu', markets: 'h2h', oddsFormat: 'decimal', dateFormat: 'iso' });
  const fetchedAt = new Date().toISOString();
  const res = await fetch(`https://api.the-odds-api.com/v4/sports/${sport}/odds?${qs}`);
  const body = await res.text();
  let data = null;
  try { data = JSON.parse(body); } catch (_) { /* 非 JSON */ }
  remaining = res.headers.get('x-requests-remaining');
  const rec = { sport, status: res.status, fetchedAt, requestsUsed: res.headers.get('x-requests-used'), requestsRemaining: remaining, data };
  const file = path.join(dir, `${sport}__${stamp}.json`);
  if (fs.existsSync(file)) throw new Error('refuse to overwrite ' + file);
  fs.writeFileSync(file, JSON.stringify(rec));
  const events = Array.isArray(data) ? data.length : 0;
  const books = Array.isArray(data) ? new Set(data.flatMap((e) => (e.bookmakers || []).map((b) => b.key))) : new Set();
  summary.push(`${sport}: status ${res.status}, ${events} events, ${books.size} bookmakers${books.has('pinnacle') ? ' (含 pinnacle)' : ''}`);
  if (res.status !== 200) { summary.push('非 200，停止'); process.exitCode = 1; break; }
  await new Promise((r) => setTimeout(r, 1000));
}
summary.push(`额度剩余: ${remaining}`);
console.log(summary.join('\n'));
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, '### 国际赔率\n\n```\n' + summary.join('\n') + '\n```\n');
