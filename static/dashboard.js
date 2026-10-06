'use strict';
const $ = id => document.getElementById(id);
const number = (value, digits = 2) => value == null ? '—' : Number(value).toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits});
const distance = value => value == null ? '—' : value < 1000 ? `${number(value, 3)} ms` : `${number(value / 1000, 3)} s`;
const state = {period: 'all', data: null, sort: 'score', winnerLimit: 20};
let requestNumber = 0;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function empty(body, columns, text = 'No recorded attempts in this period.') {
  const row = element('tr'); const cell = element('td', text, 'empty-cell');
  cell.colSpan = columns; row.append(cell); body.append(row);
}
function tableRow(body, cells, className = '') {
  const row = element('tr', null, className);
  cells.forEach(cell => row.append(element('td', cell.text ?? cell, cell.className ?? '')));
  body.append(row);
}
function renderBoards() {
  const data = state.data;
  if (!data) return;
  const query = $('search').value.trim().toLowerCase();
  const board = [...data.leaderboard].sort((a, b) => (b[state.sort] ?? -Infinity) - (a[state.sort] ?? -Infinity) || a.nick.localeCompare(b.nick));
  const body = $('leaderboard-body'); body.replaceChildren();
  let previous = null, rank = 0;
  board.forEach((row, index) => {
    if (row[state.sort] !== previous || index === 0) rank = index + 1;
    previous = row[state.sort];
    if (!row.nick.toLowerCase().includes(query)) return;
    tableRow(body, [{text: String(rank).padStart(2, '0'), className: 'rank'}, {text: row.nick, className: 'player'},
      {text: number(row.score, 4), className: 'number'}, number(row.average_score), number(row.days, 0),
      number(row.attempts, 0), number(row.wins, 0), `${row.longest_streak} d`], rank === 1 ? 'top-rank' : '');
  });
  if (!body.children.length) empty(body, 8, query ? 'No players match your search.' : undefined);
  const accuracy = $('accuracy-body'); accuracy.replaceChildren();
  let accuracyRank = 0, previousMean = null;
  data.accuracy.forEach((row, index) => {
    if (index === 0 || previousMean !== row.average_offset_ms) accuracyRank = index + 1;
    previousMean = row.average_offset_ms;
    if (!row.nick.toLowerCase().includes(query)) return;
    tableRow(accuracy, [{text: String(accuracyRank).padStart(2, '0'), className: 'rank'}, {text: row.nick, className: 'player'},
      {text: distance(row.average_offset_ms), className: 'number'}, distance(row.median_offset_ms),
      distance(row.deviation_ms), distance(row.closest_ms), number(row.days, 0)]);
  });
  if (!accuracy.children.length) empty(accuracy, 7, 'No qualifying players. Try a wider period or fewer minimum days.');
}

const NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attributes = {}, text) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
  if (text != null) el.textContent = text;
  return el;
}
function chartSvg(container, width, height, label) {
  container.replaceChildren();
  const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': label});
  svg.append(svgEl('title', {}, label)); container.append(svg); return svg;
}
function renderAttendance() {
  if (!state.data) return;
  const data = state.data.attendance;
  if (!data.length) { $('attendance-chart').replaceChildren(element('p', 'No attendance to plot.', 'muted small')); return; }
  let points = data;
  const monthly = $('graph-group').value === 'month';
  if (monthly) {
    const months = new Map();
    data.forEach(row => {
      const key = row.date.slice(0, 7);
      if (!months.has(key)) months.set(key, {date: key, players: 0, attempts: 0});
      const item = months.get(key);
      item.players += row.players; item.attempts += row.attempts;
    });
    points = [...months.values()];
  }
  // Dense histories are reduced into consecutive bins; sums preserve attendance.
  if (points.length > 500) {
    const size = Math.ceil(points.length / 500), bins = [];
    for (let i = 0; i < points.length; i += size) {
      const slice = points.slice(i, i + size);
      bins.push({date: `${slice[0].date} – ${slice[slice.length - 1].date}`,
        players: slice.reduce((sum, p) => sum + p.players, 0), attempts: slice.reduce((sum, p) => sum + p.attempts, 0)});
    }
    points = bins;
  }
  const width = 1000, height = 240, left = 38, right = 25, top = 20, bottom = 35;
  const max = Math.max(1, ...points.map(p => p.attempts));
  const x = i => left + (points.length === 1 ? .5 : i / (points.length - 1)) * (width - left - right);
  const y = value => height - bottom - value / max * (height - top - bottom);
  const svg = chartSvg($('attendance-chart'), width, height,
    monthly ? 'Monthly player-days and attempts. Players are summed daily attendance, not unique monthly players.' : 'Daily attendance: unique players and total attempts.');
  for (let tick = 0; tick <= 4; tick++) {
    const value = max * tick / 4;
    svg.append(svgEl('line', {x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: '#2b2e3b', 'stroke-dasharray': '3 5'}));
    svg.append(svgEl('text', {x: left - 8, y: y(value) + 4, 'text-anchor': 'end'}, number(value, max > 4 ? 0 : 1)));
  }
  for (const [key, color] of [['attempts', '#ae8dff'], ['players', '#7198ff']]) {
    const coordinates = points.map((p, i) => `${x(i)},${y(p[key])}`).join(' ');
    svg.append(svgEl('polyline', {points: coordinates, fill: 'none', stroke: color, 'stroke-width': '2', 'stroke-linejoin': 'round'}));
  }
  points.forEach((p, i) => {
    const dot = svgEl('circle', {cx: x(i), cy: y(p.attempts), r: 4, fill: '#ae8dff', opacity: '.65'});
    dot.append(svgEl('title', {}, `${p.date}: ${p.players} ${monthly ? 'player-days' : 'players'}, ${p.attempts} attempts`)); svg.append(dot);
  });
  const indexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  indexes.forEach(i => svg.append(svgEl('text', {x: x(i), y: height - 10, 'text-anchor': i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}, points[i].date)));
  if (monthly) $('attendance-chart').append(element('p', 'Monthly blue line = player-days (daily attendance summed).', 'small muted'));
}

function renderHeatmap() {
  if (!state.data) return;
  const year = Number($('heat-year').value || state.data.anchor.slice(0, 4));
  const first = new Date(Date.UTC(year, 0, 1)), last = new Date(Date.UTC(year, 11, 31));
  const start = new Date(first); start.setUTCDate(start.getUTCDate() - (start.getUTCDay() + 6) % 7);
  const map = new Map(state.data.attendance.map(p => [p.date, p]));
  const max = Math.max(1, ...state.data.attendance.map(p => p.players));
  const colors = ['#242734', '#28385b', '#365895', '#527fe0', '#92b0ff'];
  const svg = chartSvg($('heatmap'), 960, 144, `${year} attendance calendar, Monday to Sunday.`);
  ['Mon', '', 'Wed', '', 'Fri', '', 'Sun'].forEach((day, i) => svg.append(svgEl('text', {x: 0, y: 38 + i * 15}, day)));
  const period = state.data.period, anchor = new Date(`${state.data.anchor}T00:00:00Z`);
  const lower = new Date(anchor), upper = new Date(anchor);
  if (period === 'day') upper.setUTCDate(upper.getUTCDate() + 1);
  if (period === 'week') {lower.setUTCDate(lower.getUTCDate() - (lower.getUTCDay() + 6) % 7); upper.setTime(lower.getTime()); upper.setUTCDate(upper.getUTCDate() + 7);}
  if (period === 'month') {lower.setUTCDate(1); upper.setTime(lower.getTime()); upper.setUTCMonth(upper.getUTCMonth() + 1);}
  if (period === 'year') {lower.setUTCMonth(0, 1); upper.setUTCFullYear(lower.getUTCFullYear() + 1, 0, 1);}
  for (let cursor = new Date(first); cursor <= last; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    const key = cursor.toISOString().slice(0, 10), row = map.get(key);
    const week = Math.floor((cursor - start) / 86400000 / 7), weekday = (cursor.getUTCDay() + 6) % 7;
    const outside = period !== 'all' && (cursor < lower || cursor >= upper);
    const level = row?.players ? Math.max(1, Math.ceil(row.players / max * 4)) : 0;
    const label = `${key}: ${row?.players ?? 0} players, ${row?.attempts ?? 0} attempts${outside ? ', outside selection' : ''}`;
    const rect = svgEl('rect', {x: 40 + week * 17, y: 27 + weekday * 15, width: 13, height: 11, rx: 2,
      fill: outside ? '#14161d' : colors[level], stroke: outside ? '#323644' : 'none', tabindex: 0, role: 'img', 'aria-label': label});
    rect.append(svgEl('title', {}, label)); svg.append(rect);
    if (cursor.getUTCDate() === 1) svg.append(svgEl('text', {x: 40 + week * 17, y: 14}, cursor.toLocaleString(undefined, {month: 'short', timeZone: 'UTC'})));
  }
}

function renderTiming() {
  const values = state.data.histogram;
  const width = 600, height = 250, left = 28, right = 10, top = 30, bottom = 35;
  const svg = chartSvg($('timing-chart'), width, height, 'Attempts by second during 13:37, with target at second 37.');
  const max = Math.max(1, ...values), bin = (width - left - right) / 60;
  for (let tick = 0; tick < 3; tick++) {
    const value = max * tick / 2, y = height - bottom - value / max * (height - top - bottom);
    svg.append(svgEl('line', {x1: left, x2: width - right, y1: y, y2: y, stroke: '#2b2e3b', 'stroke-dasharray': '3 5'}));
    svg.append(svgEl('text', {x: left - 6, y: y + 4, 'text-anchor': 'end'}, number(value, 0)));
  }
  values.forEach((value, second) => {
    const barHeight = value / max * (height - top - bottom);
    const bar = svgEl('rect', {x: left + second * bin, y: height - bottom - barHeight,
      width: bin - 2, height: barHeight, rx: 1, fill: second === 37 ? '#ae8dff' : '#668dec'});
    bar.append(svgEl('title', {}, `13:37:${String(second).padStart(2, '0')}: ${value} attempts`)); svg.append(bar);
  });
  const targetX = left + 37 * bin;
  svg.append(svgEl('line', {x1: targetX, x2: targetX, y1: top - 8, y2: height - bottom, stroke: '#c6b1ff', 'stroke-dasharray': '3 3'}));
  svg.append(svgEl('text', {x: targetX, y: top - 13, 'text-anchor': 'middle'}, 'TARGET :37'));
  [0, 10, 20, 30, 40, 50, 59].forEach(s => svg.append(svgEl('text', {x: left + s * bin, y: height - 12, 'text-anchor': 'middle'}, `:${String(s).padStart(2, '0')}`)));
  const summary = state.data.summary, nerd = $('nerd-stats'); nerd.replaceChildren();
  const total = summary.early + summary.late + summary.exact;
  const stats = [['Average score', number(summary.average_score)], ['Median absolute distance', distance(summary.median_offset_ms)],
    ['Early / late / exact', `${summary.early} / ${summary.late} / ${summary.exact}`],
    ['Within 100 ms', `${summary.within_100ms} (${number(total ? summary.within_100ms / total * 100 : 0, 1)}%)`],
    ['Attempts per active day', number(summary.active_days ? summary.attempts / summary.active_days : null)],
    ['Longest attendance streak', `${Math.max(0, ...state.data.leaderboard.map(r => r.longest_streak))} consecutive days`]];
  stats.forEach(([label, value]) => nerd.append(element('dt', label), element('dd', value)));
}

function renderWinners() {
  if (!state.data) return;
  const key = $('winner-period').value;
  const rows = state.data[`${key}_winners`];
  const body = $('winners-body'); body.replaceChildren();
  rows.slice(0, state.winnerLimit).forEach(row => tableRow(body, [row.date || row.period,
    {text: row.nicks.join(' + '), className: 'player'}, {text: number(row.score, 4), className: 'number'},
    row.participants ?? '—', row.attempts ?? '—']));
  if (!rows.length) empty(body, 5, 'No champions in this selection.');
  $('winner-count').textContent = `${rows.length} ${key === 'daily' ? 'DAYS' : key === 'monthly' ? 'MONTHS' : 'YEARS'}`;
  $('more-winners').hidden = rows.length <= state.winnerLimit;
}

function render(data) {
  const summary = data.summary;
  $('total-attempts').textContent = number(summary.attempts, 0);
  $('active-days').textContent = `${summary.active_days} active days`;
  $('total-players').textContent = number(summary.players, 0);
  $('closest').textContent = distance(summary.closest ? Math.abs(summary.closest.offset_us) / 1000 : null);
  $('closest-user').textContent = summary.closest ? `${summary.closest.nick} · ${summary.closest.game_date}` : 'No timed attempts in selection';
  $('record-score').textContent = number(summary.all_time_high?.score, 4);
  $('record-user').textContent = summary.all_time_high ? `${summary.all_time_high.nick} · entire archive` : 'No scores yet';
  $('timezone').textContent = $('sidebar-zone').textContent = data.timezone;
  $('anchor').value ||= data.anchor;
  $('anchor').disabled = state.period === 'all';
  $('board-period').textContent = state.period === 'all' ? 'ALL TIME' : state.period.toUpperCase();
  $('scope').textContent = state.period === 'all' ? 'Showing the entire archive. Choose a period to explore a calendar day, week, month, or year.' : `Showing the ${state.period} containing ${data.anchor} · ${data.timezone}. The all-time record always uses the full archive.`;
  const oldYear = $('heat-year').value;
  const years = [...new Set([...data.years, data.anchor.slice(0, 4)])].sort().reverse();
  $('heat-year').replaceChildren(...years.map(year => {const opt = element('option', year); opt.value = year; return opt;}));
  const heatScope = `${data.period}:${data.anchor}`;
  const selectedYear = data.period === 'all' ? data.latest_date?.slice(0, 4) || data.anchor.slice(0, 4) : data.anchor.slice(0, 4);
  $('heat-year').value = state.heatScope === heatScope && years.includes(oldYear) ? oldYear : selectedYear;
  state.heatScope = heatScope;
  const attempts = $('attempts-body'); attempts.replaceChildren();
  data.recent.forEach(row => tableRow(attempts, [row.game_date, {text: row.nick, className: 'player'}, row.local_time || '—',
    row.offset_us == null ? '—' : `${distance(Math.abs(row.offset_us) / 1000)} ${row.offset_us < 0 ? 'early' : row.offset_us > 0 ? 'late' : 'exact'}`,
    {text: number(row.score, 4), className: 'number'}, row.source === 'live' ? 'Live' : 'Imported']));
  if (!data.recent.length) empty(attempts, 6);
  $('import-note').textContent = data.import ? `History imported: ${data.import.attempts} recorded attempts, ${data.import.daily_summaries} summary-only daily records. Original scores retained.` : 'Scores are stored transactionally in SQLite.';
  renderBoards(); renderAttendance(); renderHeatmap(); renderTiming(); renderWinners();
}

async function load() {
  const serial = ++requestNumber;
  $('status').textContent = 'Updating…'; $('error').hidden = true;
  const params = new URLSearchParams({period: state.period, min_days: $('min-days').value || '5'});
  if ($('anchor').value) params.set('date', $('anchor').value);
  try {
    const response = await fetch(`/api/stats?${params}`, {credentials: 'same-origin'});
    if (serial !== requestNumber) return;
    if (response.status === 401) { location.assign('/login'); return; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load statistics.');
    state.data = data; render(data);
    $('status').textContent = `Updated ${new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
  } catch (error) {
    if (serial !== requestNumber) return;
    $('error').textContent = `${error.message} The next refresh will retry.`; $('error').hidden = false;
    $('status').textContent = 'Update failed';
  }
}
document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => {
  state.period = button.dataset.period; state.winnerLimit = 20;
  document.querySelectorAll('[data-period]').forEach(b => {b.classList.toggle('selected', b === button); b.setAttribute('aria-pressed', b === button ? 'true' : 'false');});
  load();
}));
document.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => {
  state.sort = button.dataset.sort;
  document.querySelectorAll('[data-sort]').forEach(b => {b.textContent = b.textContent.replace(' ↓', '') + (b === button ? ' ↓' : ''); b.closest('th').setAttribute('aria-sort', b === button ? 'descending' : 'none');});
  renderBoards();
}));
$('anchor').addEventListener('change', load);
$('min-days').addEventListener('change', load);
$('search').addEventListener('input', renderBoards);
$('graph-group').addEventListener('change', renderAttendance);
$('heat-year').addEventListener('change', renderHeatmap);
$('winner-period').addEventListener('change', () => {state.winnerLimit = 20; renderWinners();});
$('more-winners').addEventListener('click', () => {state.winnerLimit += 30; renderWinners();});
$('latest').addEventListener('click', () => {if (state.data?.latest_date) {$('anchor').value = state.data.latest_date; if (state.period === 'all') document.querySelector('[data-period="day"]').click(); else load();}});
document.querySelectorAll('.sidebar nav a').forEach(link => link.addEventListener('click', () => {
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.toggle('active', a === link));
}));
load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
