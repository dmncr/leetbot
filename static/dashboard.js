'use strict';
const $ = id => document.getElementById(id);
const number = (value, digits = 2) => value == null ? '—' : Number(value).toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits});
const distance = value => value == null ? '—' : value < 1000 ? `${number(value, 3)} ms` : `${number(value / 1000, 3)} s`;
const initialParams = new URLSearchParams(location.search);
const state = {period: ['day', 'week', 'month', 'year', 'all'].includes(initialParams.get('period')) ? initialParams.get('period') : 'all',
  minDays: initialParams.get('min_days') || '5', data: null, sort: 'score', winnerLimit: 20};
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
  const accuracy = $('accuracy-body');
  if (!accuracy) return;
  accuracy.replaceChildren();
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
  if (!state.data || !$('attendance-chart')) return;
  const data = state.data.attendance;
  if (!data.length) { $('attendance-chart').replaceChildren(element('p', 'No attendance to plot.', 'muted small')); return; }
  let points = data;
  const monthly = $('graph-group').value === 'month';
  let grouped = monthly;
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
    grouped = true;
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
    grouped ? 'Grouped player-days and attempts. Players are summed daily attendance.' : 'Daily attendance: unique players and total attempts.');
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
    dot.append(svgEl('title', {}, `${p.date}: ${p.players} ${grouped ? 'player-days' : 'players'}, ${p.attempts} attempts`)); svg.append(dot);
  });
  const indexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  indexes.forEach(i => svg.append(svgEl('text', {x: x(i), y: height - 10, 'text-anchor': i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}, points[i].date)));
  if (grouped) $('attendance-chart').append(element('p', `${monthly ? 'Monthly' : 'Grouped'} blue line = player-days (daily attendance summed).`, 'small muted'));
}

function renderHeatmap() {
  if (!state.data || !$('heatmap')) return;
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

function receiptTime(offsetUs) {
  const milliseconds = 36500 + (offsetUs + 500000) / 1000;
  return `13:37:${String(Math.floor(milliseconds / 1000)).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`;
}

function renderTimingWindow() {
  const container = $('timing-window-chart');
  if (!container) return;
  const window = state.data.timing_window;
  const total = window.included + window.outside;
  $('timing-window-note').textContent = `${number(window.included, 0)} of ${number(total, 0)} timed attempts inside this window · ${number(window.outside, 0)} outside${window.unknown ? ` · ${window.unknown} without timing` : ''}. Both endpoints included.`;
  if (!window.included) {
    container.replaceChildren(element('p', 'No attempts between 13:37:36.500 and 13:37:37.500 in this selection.', 'muted small'));
    return;
  }
  const width = 640, height = 270, left = 38, right = 22, top = 30, bottom = 55;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const max = Math.max(...window.bins), binWidth = plotWidth / window.bins.length;
  const x = offset => left + (offset - window.start_us) / (window.end_us - window.start_us) * plotWidth;
  const y = count => height - bottom - count / max * plotHeight;
  const svg = chartSvg(container, width, height, `Attempts received from 13:37:36.500 to 13:37:37.500 in 25 millisecond bins. ${window.included} attempts. Target 13:37:37.000.`);
  svg.append(svgEl('rect', {x: left, y: top, width: plotWidth / 2, height: plotHeight, fill: '#7198ff', opacity: '.04'}));
  svg.append(svgEl('rect', {x: x(0), y: top, width: plotWidth / 2, height: plotHeight, fill: '#ae8dff', opacity: '.04'}));
  const ticks = [...new Set([0, Math.ceil(max / 2), max])];
  ticks.forEach(count => {
    svg.append(svgEl('line', {x1: left, x2: width - right, y1: y(count), y2: y(count), stroke: '#2b2e3b', 'stroke-dasharray': '3 5'}));
    svg.append(svgEl('text', {x: left - 8, y: y(count) + 4, 'text-anchor': 'end'}, number(count, 0)));
  });
  window.bins.forEach((count, index) => {
    const start = window.start_us + index * window.bin_us, end = start + window.bin_us;
    const label = `${receiptTime(start)}–${receiptTime(end)} (${index === window.bins.length - 1 ? 'inclusive' : 'end exclusive'}): ${count} attempts`;
    const group = svgEl('g', {tabindex: 0, role: 'img', 'aria-label': label});
    group.append(svgEl('title', {}, label));
    group.append(svgEl('rect', {x: left + index * binWidth, y: top, width: binWidth, height: plotHeight, fill: 'transparent'}));
    group.append(svgEl('rect', {x: left + index * binWidth + 1, y: y(count), width: binWidth - 2, height: count / max * plotHeight,
      fill: start < 0 ? '#7198ff' : '#ae8dff', rx: 1}));
    svg.append(group);
  });
  svg.append(svgEl('line', {x1: x(0), x2: x(0), y1: top - 7, y2: height - bottom, stroke: '#78dca9', 'stroke-dasharray': '3 3'}));
  svg.append(svgEl('text', {x: x(0), y: top - 14, 'text-anchor': 'middle', class: 'target-label'}, 'TARGET'));
  [-500000, -250000, 0, 250000, 500000].forEach(offset => {
    const anchor = offset === window.start_us ? 'start' : offset === window.end_us ? 'end' : 'middle';
    svg.append(svgEl('text', {x: x(offset), y: height - bottom + 20, 'text-anchor': anchor}, receiptTime(offset).slice(6)));
  });
  svg.append(svgEl('text', {x: left, y: height - 8}, '← EARLY'));
  svg.append(svgEl('text', {x: width - right, y: height - 8, 'text-anchor': 'end'}, 'LATE →'));
}

function renderTimingSplit() {
  const container = $('timing-split');
  if (!container) return;
  const summary = state.data.summary;
  const parts = [['Early', summary.early, '#7198ff'], ['Late', summary.late, '#ae8dff'], ['Exact', summary.exact, '#78dca9']];
  const total = parts.reduce((sum, part) => sum + part[1], 0);
  if (!total) {container.replaceChildren(element('p', 'No timed attempts in this selection.', 'muted small')); return;}
  const svg = chartSvg(container, 220, 220, parts.map(([label, count]) => `${label}: ${count} (${number(count / total * 100, 1)}%)`).join(', '));
  const radius = 78, circumference = 2 * Math.PI * radius;
  let offset = 0;
  parts.forEach(([label, count, color]) => {
    if (!count) return;
    const length = count / total * circumference;
    const arc = svgEl('circle', {cx: 110, cy: 110, r: radius, fill: 'none', stroke: color, 'stroke-width': 23,
      'stroke-dasharray': `${length} ${circumference - length}`, 'stroke-dashoffset': -offset, transform: 'rotate(-90 110 110)'});
    arc.append(svgEl('title', {}, `${label}: ${count} attempts`)); svg.append(arc); offset += length;
  });
  svg.append(svgEl('text', {x: 110, y: 108, 'text-anchor': 'middle', class: 'donut-total'}, number(total, 0)));
  svg.append(svgEl('text', {x: 110, y: 130, 'text-anchor': 'middle', class: 'donut-label'}, 'TIMED ATTEMPTS'));
  const legend = element('ul', null, 'split-legend');
  parts.forEach(([label, count], index) => {
    const item = element('li');
    item.append(element('span', label, `split-label split-${index}`), element('strong', `${number(count, 0)} · ${number(count / total * 100, 1)}%`));
    legend.append(item);
  });
  container.append(legend);
}

function renderTiming() {
  renderTimingWindow(); renderTimingSplit();
  if (!$('timing-chart')) return;
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
  if (!state.data || !$('winners-body')) return;
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
  if ($('heat-year')) {
    const oldYear = $('heat-year').value;
    const years = [...new Set([...data.years, data.anchor.slice(0, 4)])].sort().reverse();
    $('heat-year').replaceChildren(...years.map(year => {const opt = element('option', year); opt.value = year; return opt;}));
    const heatScope = `${data.period}:${data.anchor}`;
    const selectedYear = data.period === 'all' ? data.latest_date?.slice(0, 4) || data.anchor.slice(0, 4) : data.anchor.slice(0, 4);
    $('heat-year').value = state.heatScope === heatScope && years.includes(oldYear) ? oldYear : selectedYear;
    state.heatScope = heatScope;
  }
  const attempts = $('attempts-body');
  if (attempts) {
    attempts.replaceChildren();
    data.recent.forEach(row => tableRow(attempts, [row.game_date, {text: row.nick, className: 'player'}, row.local_time || '—',
      row.offset_us == null ? '—' : `${distance(Math.abs(row.offset_us) / 1000)} ${row.offset_us < 0 ? 'early' : row.offset_us > 0 ? 'late' : 'exact'}`,
      {text: number(row.score, 4), className: 'number'}, row.source === 'live' ? 'Live' : 'Imported']));
    if (!data.recent.length) empty(attempts, 6);
  }
  $('import-note').textContent = data.import ? `History imported: ${data.import.attempts} recorded attempts, ${data.import.daily_summaries} summary-only daily records. Original scores retained.` : 'Scores are stored transactionally in SQLite.';
  const params = new URLSearchParams({period: data.period, date: data.anchor, min_days: String(data.min_days)});
  history.replaceState(null, '', `${location.pathname}?${params}${location.hash}`);
  document.querySelectorAll('[data-page-link]').forEach(link => {
    const target = new URL(link.href);
    target.search = params.toString();
    link.href = `${target.pathname}${target.search}${target.hash}`;
  });
  renderBoards(); renderAttendance(); renderHeatmap(); renderTiming(); renderWinners();
}

async function load() {
  const serial = ++requestNumber;
  $('status').textContent = 'Updating…'; $('error').hidden = true;
  const params = new URLSearchParams({period: state.period, min_days: $('min-days')?.value || state.minDays});
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
$('min-days')?.addEventListener('change', load);
$('search').addEventListener('input', renderBoards);
$('graph-group')?.addEventListener('change', renderAttendance);
$('heat-year')?.addEventListener('change', renderHeatmap);
$('winner-period')?.addEventListener('change', () => {state.winnerLimit = 20; renderWinners();});
$('more-winners')?.addEventListener('click', () => {state.winnerLimit += 30; renderWinners();});
$('latest').addEventListener('click', () => {if (state.data?.latest_date) {$('anchor').value = state.data.latest_date; if (state.period === 'all') document.querySelector('[data-period="day"]').click(); else load();}});
document.querySelectorAll('.sidebar nav a').forEach(link => link.addEventListener('click', () => {
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.toggle('active', a === link));
}));
if (initialParams.get('date')) $('anchor').value = initialParams.get('date');
if ($('min-days')) $('min-days').value = state.minDays;
document.querySelectorAll('[data-period]').forEach(button => {
  const selected = button.dataset.period === state.period;
  button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
});
load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
