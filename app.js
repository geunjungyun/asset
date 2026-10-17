// 백엔드 API 베이스.
// FastAPI가 프론트를 함께 서빙하면(http://localhost:8000) same-origin으로 동작한다.
// file:// 로 직접 열거나 다른 포트면 localhost:8000 으로 폴백한다.
const API_BASE = (location.protocol === 'http:' || location.protocol === 'https:')
    ? ''                        // 서버가 서빙 중 → 상대경로
    : 'http://localhost:8000';  // file:// 등

const won = (n) => '₩ ' + Math.round(Number(n) || 0).toLocaleString('ko-KR');
const pct = (n) => (Number(n) >= 0 ? '+' : '') + (Number(n) || 0).toFixed(2) + '%';
const signed = (n) => (Number(n) >= 0 ? '+' : '') + Math.round(Number(n) || 0).toLocaleString('ko-KR');

// 통화 인식 금액 포맷 (USD: $1,234.56 / KRW: ₩ 1,234)
let accountCurrency = 'KRW';
function money(n, cur = accountCurrency) {
    n = Number(n) || 0;
    if (cur === 'USD') return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return '₩ ' + Math.round(n).toLocaleString('ko-KR');
}
function signedMoney(n, cur = accountCurrency) {
    n = Number(n) || 0;
    const sign = n >= 0 ? '+' : '−';
    const a = Math.abs(n);
    if (cur === 'USD') return sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return sign + Math.round(a).toLocaleString('ko-KR');
}
const curUnit = (cur = accountCurrency) => (cur === 'USD' ? '$' : '₩');

async function fetchJSON(path) {
    const res = await fetch(API_BASE + path);
    if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
    return res.json();
}

// ---------------------------------------------------------------------------
// 모드 배지 (mock / live)
// ---------------------------------------------------------------------------
function renderModeBadge(mode, env, warning) {
    const badge = document.getElementById('mode-badge');
    if (!badge) return;  // 배지 제거됨
    if (mode === 'live') {
        badge.className = 'mode-badge live';
        badge.innerHTML = `<i class="fa-solid fa-circle-check"></i> 실계좌 연동 (${env === 'paper' ? '모의' : '실전'})`;
    } else {
        badge.className = 'mode-badge mock';
        badge.innerHTML = '<i class="fa-solid fa-pen"></i> 수동 입력 (KIS 미연동)';
    }
    if (warning) badge.title = warning;
}

// ---------------------------------------------------------------------------
// 계좌 요약 + 개요 카드
// ---------------------------------------------------------------------------
let lastAccountSummary = null;
let usdKrwRate = 1380;  // USD→KRW 환율 (market usdkrw로 갱신)

// 달러 금액 아래 원화 병기 (계좌가 USD일 때만)
function updateKrwLines() {
    const s = lastAccountSummary;
    const show = s && accountCurrency === 'USD';
    const krw = (usd) => '≈ ₩' + Math.round((Number(usd) || 0) * usdKrwRate).toLocaleString('ko-KR');
    const set = (id, usd, signed) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (!show) { el.textContent = ''; return; }
        const v = Math.round(Math.abs(usd) * usdKrwRate).toLocaleString('ko-KR');
        el.textContent = signed ? ((usd >= 0 ? '≈ +₩' : '≈ −₩') + v) : ('≈ ₩' + v);
    };
    if (!show) {
        ['total-asset-krw','cash-asset-krw','invest-asset-krw','sum-purchase-krw','sum-eval-krw','sum-pnl-krw']
            .forEach(id => { const e = document.getElementById(id); if (e) e.textContent = ''; });
        return;
    }
    set('total-asset-krw', s.total_asset);
    set('cash-asset-krw', s.cash);
    set('invest-asset-krw', s.invest_eval);
    set('sum-purchase-krw', s.purchase_amount);
    set('sum-eval-krw', s.invest_eval);
    set('sum-pnl-krw', s.eval_pnl, true);
    updatePnlKrw();
    if (typeof holdingsList !== 'undefined' && holdingsList.length) renderHoldingsPie(holdingsList);
}

// 보유종목 표: 평가손익(USD) 옆/아래 원화 병기
function krwSignedStr(usd) {
    if (accountCurrency !== 'USD') return '';
    const won = Math.round(Math.abs(Number(usd) || 0) * usdKrwRate).toLocaleString('ko-KR');
    return (usd >= 0 ? '≈ +₩' : '≈ −₩') + won;
}
function krwStr(usd) {
    if (accountCurrency !== 'USD') return '';
    return '≈ ₩' + Math.round((Number(usd) || 0) * usdKrwRate).toLocaleString('ko-KR');
}
function updatePnlKrw() {
    document.querySelectorAll('.pnl-krw').forEach(el => {
        el.textContent = krwSignedStr(parseFloat(el.dataset.usd));
    });
    document.querySelectorAll('.val-krw').forEach(el => {
        el.textContent = krwStr(parseFloat(el.dataset.usd));
    });
}

function renderAccount(data) {
    const s = data.summary;
    accountCurrency = data.currency || 'KRW';
    lastAccountSummary = s;
    if (livePrices['usdkrw']) usdKrwRate = livePrices['usdkrw'].value;

    document.getElementById('total-asset').textContent = money(s.total_asset);
    document.getElementById('cash-asset').textContent = money(s.cash);
    document.getElementById('invest-asset').textContent = money(s.invest_eval);

    const investTrend = document.getElementById('invest-trend');
    const up = s.pnl_rate >= 0;
    investTrend.className = 'trend ' + (up ? 'positive' : 'negative');
    investTrend.innerHTML = `<i class="fa-solid fa-arrow-trend-${up ? 'up' : 'down'}"></i> ${pct(s.pnl_rate)}`;

    // 요약 통계
    document.getElementById('sum-purchase').textContent = money(s.purchase_amount);
    document.getElementById('sum-eval').textContent = money(s.invest_eval);

    const pnlEl = document.getElementById('sum-pnl');
    pnlEl.textContent = signedMoney(s.eval_pnl);
    pnlEl.className = 'value ' + (s.eval_pnl >= 0 ? 'positive' : 'negative');

    const rateEl = document.getElementById('sum-rate');
    rateEl.textContent = pct(s.pnl_rate);
    rateEl.className = 'value ' + (s.pnl_rate >= 0 ? 'positive' : 'negative');

    if (data.updated_at) {
        document.getElementById('updated-at').textContent =
            '기준: ' + data.updated_at.replace('T', ' ');
    }

    updateKrwLines();
    renderHoldings(data.holdings || []);
    renderModeBadge(data.mode, data.env, data.warning);
}

let holdingsList = [];
let domesticList = [];   // 국내주식(원화) 보유목록 — 차트 평단/수익률 조회용

// 보유종목 표시 순서(고정). 목록에 없는 코드는 뒤로.
const HOLDING_ORDER = ['SNDK', 'MU', 'MRVL', 'AMD', 'GOOGL', 'NVDA', 'AMGN', 'LLY', '000660'];
function holdingOrderIdx(code) {
    const i = HOLDING_ORDER.indexOf(code);
    return i < 0 ? 999 : i;
}
function sortByHoldingOrder(arr, getCode) {
    return (arr || []).slice().sort((a, b) => holdingOrderIdx(getCode(a)) - holdingOrderIdx(getCode(b)));
}

function renderHoldings(holdings) {
    const body = document.getElementById('holdings-body');
    body.innerHTML = '';
    holdingsList = sortByHoldingOrder(holdings || [], h => h.code);   // 지정 순서로 정렬
    ensureTargetStopDefaults();   // 목표/손절 초기값(평단 ±15%)

    if (!holdingsList.length) {
        body.innerHTML = '<tr><td colspan="7" class="loading-row">보유 중인 종목이 없습니다.</td></tr>';
        renderHoldingsPie([]);
        return;
    }

    holdingsList.forEach(h => {
        const up = h.eval_pnl >= 0;
        const cls = up ? 'positive' : 'negative';
        const tr = document.createElement('tr');
        tr.className = 'holding-row';
        tr.dataset.code = h.code;
        tr.dataset.name = h.name;
        tr.innerHTML = `
            <td>
                <div class="stock-name">${h.name}</div>
                <div class="stock-code">${h.code}</div>
            </td>
            <td class="num">${Number(h.quantity).toLocaleString('ko-KR')}</td>
            <td class="num">${money(h.avg_price)}</td>
            <td class="num">${money(h.current_price)}</td>
            <td class="num">${money(h.eval_amount)}<span class="val-krw" data-usd="${h.eval_amount}"></span></td>
            <td class="num ${cls}">${signedMoney(h.eval_pnl)}<span class="pnl-krw" data-usd="${h.eval_pnl}"></span></td>
            <td class="num">
                <div class="ret-total ${cls}">${pct(h.pnl_rate)}</div>
                ${h.prdy_pct != null ? `<div class="ret-prdy ${h.prdy_pct >= 0 ? 'positive' : 'negative'}">전일 ${h.prdy_pct >= 0 ? '+' : ''}${h.prdy_pct.toFixed(2)}%</div>` : ''}
            </td>
        `;
        body.appendChild(tr);
    });

    updatePnlKrw();
    renderHoldingsPie(holdingsList);
}

// 국내주식(원화) 섹션 — 메리츠 국내 계좌
function renderDomestic(d) {
    const block = document.getElementById('domestic-block');
    const body = document.getElementById('domestic-body');
    if (!block || !body) return;
    const hold = (d && d.holdings) || [];
    domesticList = hold;   // 차트에서 평단/수익률 조회용
    if (!hold.length) { block.hidden = true; return; }
    body.innerHTML = '';
    hold.forEach(h => {
        const up = (h.eval_pnl || 0) >= 0;
        const cls = up ? 'positive' : 'negative';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>
                <div class="stock-name">${h.name}</div>
                <div class="stock-code">${h.code}</div>
            </td>
            <td class="num">${Number(h.quantity).toLocaleString('ko-KR')}</td>
            <td class="num">${won(h.avg_price)}</td>
            <td class="num">${won(h.current_price)}</td>
            <td class="num">${won(h.eval_amount)}</td>
            <td class="num ${cls}">${up ? '+' : '−'}${won(Math.abs(h.eval_pnl))}</td>
            <td class="num">
                <div class="ret-total ${cls}">${pct(h.pnl_rate)}</div>
                ${h.prdy_pct != null ? `<div class="ret-prdy ${h.prdy_pct >= 0 ? 'positive' : 'negative'}">전일 ${h.prdy_pct >= 0 ? '+' : ''}${h.prdy_pct.toFixed(2)}%</div>` : ''}
            </td>`;
        body.appendChild(tr);
    });
    const s = d.summary || {};
    const up = (s.eval_pnl || 0) >= 0;
    const sub = document.getElementById('domestic-sub');
    if (sub) sub.innerHTML =
        `평가 ${won(s.invest_eval)} · <span class="${up ? 'positive' : 'negative'}">`
        + `${up ? '+' : '−'}${won(Math.abs(s.eval_pnl))} (${pct(s.pnl_rate)})</span>`
        + (s.cash ? ` · 예수금 ${won(s.cash)}` : '');
    block.hidden = false;
}

async function loadDomestic() {
    try {
        renderDomestic(await fetchJSON('/api/account/domestic'));
    } catch (err) {
        console.error(err);
    }
}

// 평가금액 비중 도넛 차트
const PIE_COLORS = ['#3b82f6', '#8b5cf6', '#f59e0b', '#10b981', '#ef4444',
                    '#06b6d4', '#ec4899', '#a3e635', '#f97316', '#14b8a6'];

function pieArc(cx, cy, rO, rI, a0, a1) {
    const pt = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const large = (a1 - a0) > Math.PI ? 1 : 0;
    const [x1, y1] = pt(rO, a0), [x2, y2] = pt(rO, a1);
    const [x3, y3] = pt(rI, a1), [x4, y4] = pt(rI, a0);
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${rO} ${rO} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
        + ` L${x3.toFixed(2)} ${y3.toFixed(2)} A${rI} ${rI} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)} Z`;
}

function renderHoldingsPie(holdings) {
    const svg = document.getElementById('holdings-pie');
    const legend = document.getElementById('holdings-pie-legend');
    if (!svg || !legend) return;

    const items = (holdings || [])
        .map((h, i) => ({ name: h.name, code: h.code, val: Number(h.eval_amount) || 0, pnl: Number(h.eval_pnl) || 0 }))
        .filter(x => x.val > 0)
        .sort((a, b) => b.val - a.val);
    const total = items.reduce((s, x) => s + x.val, 0);
    const totalPnl = items.reduce((s, x) => s + x.pnl, 0);

    if (!items.length || total <= 0) {
        svg.innerHTML = '';
        legend.innerHTML = '<div class="loading-row">데이터 없음</div>';
        return;
    }

    const cx = 110, cy = 110, rO = 100, rI = 60;
    let a = -Math.PI / 2;  // 12시 방향 시작
    let paths = '';
    items.forEach((x, i) => {
        const frac = x.val / total;
        const a1 = a + frac * Math.PI * 2;
        const color = PIE_COLORS[i % PIE_COLORS.length];
        x.color = color;
        x.pct = frac * 100;
        // 단일 종목(100%)은 풀 링으로
        if (frac >= 0.9999) {
            paths += `<circle cx="${cx}" cy="${cy}" r="${(rO + rI) / 2}" fill="none"
                stroke="${color}" stroke-width="${rO - rI}"/>`;
        } else {
            paths += `<path d="${pieArc(cx, cy, rO, rI, a, a1)}" fill="${color}"
                stroke="var(--card-bg)" stroke-width="1.5"/>`;
        }
        a = a1;
    });

    // 가운데 합계 (평가금액 + 원화) — 색은 전체 손익 부호
    const krwLine = krwStr(total);
    const totalColor = totalPnl >= 0 ? 'var(--positive)' : 'var(--negative)';
    const center = `<text x="${cx}" y="${cy - 14}" text-anchor="middle"
            fill="var(--text-muted)" font-size="11">평가금액</text>
        <text x="${cx}" y="${cy + 4}" text-anchor="middle"
            fill="${totalColor}" font-size="15" font-weight="700">${money(total)}</text>
        ${krwLine ? `<text x="${cx}" y="${cy + 20}" text-anchor="middle"
            fill="var(--text-main)" font-size="9.5">${krwLine}</text>` : ''}`;
    svg.innerHTML = paths + center;

    // 평가금액 색: 해당 종목 손익 부호(초록/빨강) — 표의 평가손익과 동일
    legend.innerHTML = items.map(x => {
        const cls = x.pnl >= 0 ? 'positive' : 'negative';
        return `
        <div class="pie-leg-item">
            <span class="pie-dot" style="background:${x.color}"></span>
            <span class="pie-leg-name">${x.name}</span>
            <span class="pie-leg-pct">${x.pct.toFixed(1)}%</span>
            <span class="pie-leg-val ${cls}">${money(x.val)}</span>
            <span class="pie-leg-krw">${krwStr(x.val)}</span>
        </div>`;
    }).join('');
}

// ---------------------------------------------------------------------------
// 보유종목 전체 차트 (실시간 / 일 / 월) — 한 화면에 모두
// ---------------------------------------------------------------------------
let holdingPeriod = '1D';
let holdingTimer = null;

// 장중(분봉) 기간 여부: 1h~12h 또는 1일. 정규장 시작/종료 세로 표기 대상.
function isIntradayPeriod() {
    return /^\d{1,2}h$/.test(holdingPeriod) || holdingPeriod === '1D';
}

let lastChartsData = null;  // 마지막 조회 결과(목표/손절 변경 시 재조회 없이 선만 갱신)

async function loadHoldingsCharts() {
    const grid = document.getElementById('holdings-charts-grid');
    if (!grid) return;
    const reqPeriod = holdingPeriod;  // 요청 시점의 기간
    try {
        const d = await fetchJSON(`/api/account/holdings-charts?period=${reqPeriod}`);
        if (reqPeriod !== holdingPeriod) return;  // 더 최신 기간 요청이 있으면 이 응답은 버림(레이스 방지)
        lastChartsData = d;
        renderHoldingsChartsGrid(d);
    } catch (err) {
        console.error(err);
        if (reqPeriod === holdingPeriod)
            grid.innerHTML = '<div class="loading-row">차트를 불러올 수 없습니다.</div>';
    }
}

function renderHoldingsChartsGrid(d) {
    const grid = document.getElementById('holdings-charts-grid');
    if (!grid || !d) return;
    grid.innerHTML = '';
    sortByHoldingOrder(d.charts || [], ch => ch.code).forEach(ch => {
        const up = (ch.change || 0) >= 0;
        const cur = ch.currency || accountCurrency;
        const cls = up ? 'positive' : 'negative';
        const held = (holdingsList || []).find(h => h.code === ch.code)
                  || (domesticList || []).find(h => h.code === ch.code);
        // 전체 수익률(매입평균가 대비). 계좌 pnl_rate 우선, 없으면 평단으로 계산.
        let totRet = null;
        if (held && held.pnl_rate != null) totRet = Number(held.pnl_rate);
        else if (held && Number(held.avg_price) > 0 && ch.value)
            totRet = (ch.value - Number(held.avg_price)) / Number(held.avg_price) * 100;
        const retCls = (totRet || 0) >= 0 ? 'positive' : 'negative';
        // 기준선: 평단(회색) + 목표(초록) + 손절(빨강)
        const lines = [];
        if (showAvg && held && Number.isFinite(Number(held.avg_price)))
            lines.push({ value: Number(held.avg_price), label: '평단', kind: 'avg' });
        const ts = targetStops[ch.code] || {};
        if (showTargetStop) {   // 옵션: 목표·손절 선 표시
            if (Number.isFinite(Number(ts.target)) && ts.target > 0)
                lines.push({ value: Number(ts.target), label: '목표', kind: 'target' });
            if (Number.isFinite(Number(ts.stop)) && ts.stop > 0)
                lines.push({ value: Number(ts.stop), label: '손절', kind: 'stop' });
        }
        const card = document.createElement('div');
        card.className = 'hc-card';
        card.innerHTML = `
            <div class="hc-top">
                <span class="hc-name">${ch.name || ch.code}</span>
                <span class="hc-code">${ch.code}</span>
            </div>
            <div class="hc-valrow">
                <span class="hc-val">${money(ch.value, cur)}</span>
                <span class="hc-chg ${cls}">${up ? '▲' : '▼'} ${pct(ch.change_pct)}</span>
                ${totRet != null ? `<span class="hc-ret ${retCls}" title="전체 수익률(매입평균가 대비)">수익 ${pct(totRet)}</span>` : ''}
            </div>
            ${chartBlock(ch.series, ch.times, up, curUnit(cur), false, lines, isIntradayPeriod() ? sessionMarks(ch.times, cur) : [])}
        `;
        grid.appendChild(card);
    });
    if (!grid.children.length) {
        grid.innerHTML = '<div class="loading-row">보유 종목이 없습니다.</div>';
    }
}

function initHoldingToggle() {
    const toggle = document.getElementById('hd-period-toggle');
    toggle.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        holdingPeriod = btn.dataset.period;
        loadHoldingsCharts();
        scheduleHoldingRefresh();
    });
    scheduleHoldingRefresh();
}

// 장중 기간(1h~5D)일 때 주기 갱신: 장중 10초, 장 밖 ≈60초
let holdingTick = 0;
function scheduleHoldingRefresh() {
    if (holdingTimer) { clearInterval(holdingTimer); holdingTimer = null; }
    if (/^[1-5]D$/.test(holdingPeriod) || /^\d{1,2}h$/.test(holdingPeriod)) {
        holdingTick = 0;
        holdingTimer = setInterval(() => {
            if (document.hidden) return;
            holdingTick++;
            if (isMarketActive() || holdingTick % IDLE_EVERY === 0) loadHoldingsCharts();
        }, LIVE_REFRESH_MS);
    }
}

// ---------------------------------------------------------------------------
// 리밸런싱 (목표 비중 → 조정 수량)
// ---------------------------------------------------------------------------
const RB_KEY = 'assetpro.targetWeights';
let targetWeights = {};
try { targetWeights = JSON.parse(localStorage.getItem(RB_KEY) || '{}') || {}; } catch (_) { targetWeights = {}; }
function saveTargets() { try { localStorage.setItem(RB_KEY, JSON.stringify(targetWeights)); } catch (_) {} }

// 목표가/손절가 (종목별)
const TS_KEY = 'assetpro.targetStops';
let targetStops = {};
try { targetStops = JSON.parse(localStorage.getItem(TS_KEY) || '{}') || {}; } catch (_) { targetStops = {}; }
function saveTargetStops() { try { localStorage.setItem(TS_KEY, JSON.stringify(targetStops)); } catch (_) {} }

// 차트에 목표·손절 선 표시 옵션
const OPT_TS_KEY = 'assetpro.showTargetStop';
let showTargetStop = true;
try { const v = localStorage.getItem(OPT_TS_KEY); if (v !== null) showTargetStop = v === '1'; } catch (_) {}

// 차트에 평단(매입평균가) 선 표시 옵션
const OPT_AVG_KEY = 'assetpro.showAvg';
let showAvg = true;
try { const v = localStorage.getItem(OPT_AVG_KEY); if (v !== null) showAvg = v === '1'; } catch (_) {}

// 도달 시 매도 비율(%) — 목표(익절)/손절
const SR_KEY = 'assetpro.sellRatios';
let sellRatios = { target: 100, stop: 100 };
try { Object.assign(sellRatios, JSON.parse(localStorage.getItem(SR_KEY) || '{}')); } catch (_) {}
function saveSellRatios() { try { localStorage.setItem(SR_KEY, JSON.stringify(sellRatios)); } catch (_) {} }

// 초기값: 처음 보는 종목은 목표=평단×1.15, 손절=평단×0.85 (이후 사용자 수정/삭제는 존중)
function ensureTargetStopDefaults() {
    let changed = false;
    (holdingsList || []).forEach(h => {
        if (targetStops[h.code] === undefined) {
            const avg = Number(h.avg_price) || 0;
            if (avg > 0) {
                targetStops[h.code] = {
                    target: Math.round(avg * 1.15 * 100) / 100,
                    stop: Math.round(avg * 0.85 * 100) / 100,
                };
                changed = true;
            }
        }
    });
    if (changed) saveTargetStops();
}

let rightView = 'charts';

const RIGHT_TITLES = { charts: '보유종목 차트', rebalance: '목표 비중 & 리밸런싱', trade: '매매관리 (목표가·손절가)' };

function initRebalance() {
    const vt = document.getElementById('right-view-toggle');
    if (vt) vt.addEventListener('click', (e) => {
        const btn = e.target.closest('button'); if (!btn) return;
        vt.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        rightView = btn.dataset.view;
        const charts = rightView === 'charts';
        document.getElementById('holdings-charts-grid').hidden = !charts;
        document.getElementById('rebalance-panel').hidden = rightView !== 'rebalance';
        document.getElementById('trade-panel').hidden = rightView !== 'trade';
        document.getElementById('hd-period-toggle').style.display = charts ? '' : 'none';
        const optw = document.getElementById('chart-opts');
        if (optw) optw.style.display = charts ? '' : 'none';
        document.getElementById('right-title').textContent = RIGHT_TITLES[rightView] || '보유종목 차트';
        if (rightView === 'rebalance') renderRebalance();
        if (rightView === 'trade') renderTradeMgmt();
    });

    // 매도 비율(목표/손절) 핸들러
    const bindRatio = (id, key) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => {
            let v = Number(el.value); if (!(v >= 0)) v = 0; if (v > 100) v = 100;
            sellRatios[key] = v; saveSellRatios(); renderTradeMgmt();
        });
    };
    bindRatio('ts-ratio-target', 'target');
    bindRatio('ts-ratio-stop', 'stop');

    // 목표·손절 선 표시 옵션
    const optCb = document.getElementById('opt-targetstop');
    if (optCb) {
        optCb.checked = showTargetStop;
        optCb.addEventListener('change', () => {
            showTargetStop = optCb.checked;
            try { localStorage.setItem(OPT_TS_KEY, showTargetStop ? '1' : '0'); } catch (_) {}
            if (lastChartsData) renderHoldingsChartsGrid(lastChartsData);
        });
    }

    // 평단 선 표시 옵션
    const avgCb = document.getElementById('opt-avg');
    if (avgCb) {
        avgCb.checked = showAvg;
        avgCb.addEventListener('change', () => {
            showAvg = avgCb.checked;
            try { localStorage.setItem(OPT_AVG_KEY, showAvg ? '1' : '0'); } catch (_) {}
            if (lastChartsData) renderHoldingsChartsGrid(lastChartsData);
        });
    }

    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };
    on('rb-equal', () => {
        const codes = (holdingsList || []).map(h => h.code);
        const w = codes.length ? Math.round(10000 / codes.length) / 100 : 0;
        codes.forEach(c => targetWeights[c] = w);
        saveTargets(); renderRebalance();
    });
    on('rb-current', () => {
        const total = (holdingsList || []).reduce((s, h) => s + (Number(h.eval_amount) || 0), 0);
        (holdingsList || []).forEach(h => targetWeights[h.code] = total ? Math.round((h.eval_amount / total * 100) * 100) / 100 : 0);
        saveTargets(); renderRebalance();
    });
    on('rb-normalize', () => {
        const codes = (holdingsList || []).map(h => h.code);
        const sum = codes.reduce((s, c) => s + (Number(targetWeights[c]) || 0), 0);
        if (sum > 0) codes.forEach(c => targetWeights[c] = Math.round(((Number(targetWeights[c]) || 0) / sum * 100) * 100) / 100);
        saveTargets(); renderRebalance();
    });
}

function renderRebalance() {
    const body = document.getElementById('rebalance-body');
    if (!body) return;
    const holds = holdingsList || [];
    const cur = accountCurrency;
    const total = holds.reduce((s, h) => s + (Number(h.eval_amount) || 0), 0);

    if (!holds.length || total <= 0) {
        body.innerHTML = '<tr><td colspan="7" class="loading-row">보유 종목이 없습니다.</td></tr>';
        return;
    }
    // 목표비중 미설정 → 현재비중 기본
    holds.forEach(h => {
        if (targetWeights[h.code] === undefined)
            targetWeights[h.code] = Math.round((h.eval_amount / total * 100) * 100) / 100;
    });

    const rows = holds.map(h => {
        const price = Number(h.current_price) || 0, evalAmt = Number(h.eval_amount) || 0;
        const curW = evalAmt / total * 100;
        const tgtW = Number(targetWeights[h.code]) || 0;
        const tgtAmt = tgtW / 100 * total;
        const diff = tgtAmt - evalAmt;
        const qty = price ? Math.round(diff / price) : 0;   // 정수 주
        const newEval = evalAmt + qty * price;
        return { code: h.code, name: h.name, evalAmt, curW, tgtW, tgtAmt, diff, qty, newEval };
    });
    const newTotal = rows.reduce((s, r) => s + r.newEval, 0) || total;

    body.innerHTML = rows.map(r => {
        const buy = r.diff >= 0, bc = buy ? 'rb-buy' : 'rb-sell';
        return `<tr>
            <td><div class="stock-name">${r.name}</div><div class="stock-code">${r.code}</div></td>
            <td class="num">${r.curW.toFixed(1)}%</td>
            <td class="num"><input class="rb-input" type="number" step="0.1" min="0" inputmode="decimal" data-code="${r.code}" value="${r.tgtW}"></td>
            <td class="num">${money(r.tgtAmt, cur)}</td>
            <td class="num ${bc}">${buy ? '+' : '−'}${money(Math.abs(r.diff), cur)}</td>
            <td class="num ${bc}">${r.qty >= 0 ? '+' : '−'}${Math.abs(r.qty).toLocaleString('ko-KR')}주</td>
            <td class="num">${(r.newEval / newTotal * 100).toFixed(1)}%</td>
        </tr>`;
    }).join('');

    body.querySelectorAll('.rb-input').forEach(inp => {
        inp.addEventListener('change', () => {
            targetWeights[inp.dataset.code] = Math.max(0, Number(inp.value) || 0);
            saveTargets(); renderRebalance();
        });
    });

    const sum = rows.reduce((s, r) => s + r.tgtW, 0);
    const sumEl = document.getElementById('rb-sum');
    if (sumEl) {
        sumEl.textContent = `목표 합계 ${sum.toFixed(1)}%`;
        sumEl.className = 'rb-sum ' + (Math.abs(sum - 100) < 0.05 ? 'ok' : 'warn');
    }
}

// 매매관리: 종목별 목표가/손절가 입력 + 상태
function renderTradeMgmt() {
    const body = document.getElementById('trade-body');
    if (!body) return;
    const holds = holdingsList || [];
    const cur = accountCurrency;
    if (!holds.length) {
        body.innerHTML = '<tr><td colspan="8" class="loading-row">보유 종목이 없습니다.</td></tr>';
        return;
    }
    // 평단 기준 % 입력 + 가격 입력(동기화) 셀
    const cell = (code, kind, avg, val) => {
        const pct = (avg > 0 && val > 0) ? (val / avg - 1) * 100 : null;
        const tone = kind === 'target' ? 'ts-target' : 'ts-stop';
        return `<td class="num ts-cell">
            <span class="ts-pctwrap"><input class="ts-pct ${tone}" type="number" step="0.5" inputmode="decimal" data-code="${code}" data-kind="${kind}" value="${pct !== null ? pct.toFixed(1) : ''}" placeholder="%"><span class="ts-pm">%</span></span>
            <input class="ts-price ${tone}" type="number" step="0.01" min="0" inputmode="decimal" data-code="${code}" data-kind="${kind}" value="${val || ''}" placeholder="가격">
        </td>`;
    };

    // 도달 시 매도 수량·실현손익 셀
    const sellCell = (qtyHeld, ratio, refPrice, avg) => {
        if (!(refPrice > 0) || !(qtyHeld > 0)) return `<td class="num">-</td>`;
        const n = Math.round(qtyHeld * (ratio / 100));
        const realized = n * (refPrice - avg);
        const rc = realized >= 0 ? 'rb-buy' : 'rb-sell';
        return `<td class="num">
            <span class="ts-sellqty">매도 ${n.toLocaleString('ko-KR')}주</span>
            <span class="ts-real ${rc}">${realized >= 0 ? '+' : '−'}${money(Math.abs(realized), cur)}</span>
        </td>`;
    };

    body.innerHTML = holds.map(h => {
        const code = h.code, price = Number(h.current_price) || 0, avg = Number(h.avg_price) || 0;
        const qtyHeld = Number(h.quantity) || 0;
        const ts = targetStops[code] || {};
        const target = Number(ts.target) || 0, stop = Number(ts.stop) || 0;
        let status = '보유중', sc = 'ts-hold';
        if (target > 0 && price >= target) { status = '🟢 목표 도달'; sc = 'ts-target'; }
        else if (stop > 0 && price <= stop) { status = '🔴 손절 도달'; sc = 'ts-stop'; }
        return `<tr class="${sc}">
            <td><div class="stock-name">${h.name}</div><div class="stock-code">${code}</div></td>
            <td class="num">${money(price, cur)}</td>
            <td class="num">${money(avg, cur)}</td>
            ${cell(code, 'target', avg, target)}
            ${cell(code, 'stop', avg, stop)}
            ${sellCell(qtyHeld, sellRatios.target, target, avg)}
            ${sellCell(qtyHeld, sellRatios.stop, stop, avg)}
            <td class="ts-status ${sc}">${status}</td>
        </tr>`;
    }).join('');

    // 매도 비율 입력값 반영
    const rt = document.getElementById('ts-ratio-target'), rs = document.getElementById('ts-ratio-stop');
    if (rt) rt.value = sellRatios.target;
    if (rs) rs.value = sellRatios.stop;

    const avgOf = (code) => { const h = (holdingsList || []).find(x => x.code === code); return h ? Number(h.avg_price) || 0 : 0; };
    const setTS = (code, kind, priceVal) => {
        targetStops[code] = targetStops[code] || {};
        if (priceVal > 0) targetStops[code][kind] = Math.round(priceVal * 100) / 100;
        else delete targetStops[code][kind];
        saveTargetStops();
        renderTradeMgmt();
        if (lastChartsData) renderHoldingsChartsGrid(lastChartsData);  // 차트 선 갱신
    };
    // % 입력 → 가격 = 평단×(1+%/100)
    body.querySelectorAll('.ts-pct').forEach(inp => inp.addEventListener('change', () => {
        const avg = avgOf(inp.dataset.code);
        setTS(inp.dataset.code, inp.dataset.kind, (inp.value === '' || !avg) ? 0 : avg * (1 + Number(inp.value) / 100));
    }));
    // 가격 직접 입력
    body.querySelectorAll('.ts-price').forEach(inp => inp.addEventListener('change', () => {
        setTS(inp.dataset.code, inp.dataset.kind, inp.value === '' ? 0 : Number(inp.value));
    }));
}

// ---------------------------------------------------------------------------
// 시장 지표 (국채금리 / 유가 / 환율)
// ---------------------------------------------------------------------------
let marketData = null;
let currentPeriod = '1W';   // 지표 그래프 초기 기간(상세)
let currentView = 'dashboard';  // 좌측 메뉴 보기: 'dashboard' | 'sector'
let sectorPeriod = '1W';        // 섹터 추이 페이지 기간(기본 1주)
let sectorData = null;          // /api/sectors 결과 (섹터 ETF 11개)
let sectorLoaded = false;
let sectorMode = 'momentum';    // 섹터 추이 보기: 'momentum' | 'chart'
const expandedSectors = new Set();  // (그래프) 펼쳐진 섹터 key
const memberCache = {};             // 'key|period' -> members[]
const expandedMom = new Set();      // (모멘텀) 펼쳐진 섹터 key
const momCache = {};                // key -> members[] (모멘텀)

// 사용자 보유가 포함된 섹터(모멘텀 랭킹에 ● 표시)
const HELD_SECTORS = new Set(['xlk', 'xlc', 'xlv']);

// 모멘텀: 주간 환산 속도 → 점수/가속 (백엔드와 동일 공식)
const WK_M = 4.345, WK_3M = 13.04;
function momCalc(r1w, r1m, r3m) {
    const p1w = r1w, p1m = r1m / WK_M, p3m = r3m / WK_3M;
    return { score: 0.5 * p1w + 0.3 * p1m + 0.2 * p3m, accel: p1w - p1m };
}
function accelLabel(accel) {
    if (accel >= 0.5) return { txt: '▲ 가속', cls: 'positive' };
    if (accel <= -0.5) return { txt: '▼ 둔화', cls: 'negative' };
    return { txt: '→ 유지', cls: 'flat' };
}
const pctCell = (v) => {
    const up = (v || 0) >= 0;
    return `<td class="num ${up ? 'positive' : 'negative'}">${up ? '+' : ''}${(v || 0).toFixed(2)}%</td>`;
};
const MARKET_REFRESH_MS = 60000;  // 스냅샷(금리/일봉) 폴링 주기
const LIVE_REFRESH_MS = 10000;    // 장중 라이브 갱신(계좌·장중차트) 기본 틱
const IDLE_EVERY = 6;             // 장 밖에는 6틱(≈60초)마다 한 번만 갱신

// 장중 여부(대략): 주말 데드존(토 06시 이후 ~ 월 09시 이전) 외에는 활성.
// 평일은 주간거래(낮)+프리/정규/애프터로 거의 24시간 체결이 있다.
function isMarketActive() {
    const d = new Date(), day = d.getDay(), h = d.getHours();
    if (day === 0) return false;              // 일요일
    if (day === 6 && h >= 6) return false;     // 토요일 오전 6시 이후
    if (day === 1 && h < 9) return false;      // 월요일 오전 9시 이전
    return true;
}

// KIS 실시간이 담당하는 지표(하이브리드). 나머지(금리)는 Yahoo.
const KIS_KEYS = new Set(['wti', 'brent', 'usdkrw']);
const livePrices = {};   // key -> {value, as_of}  (SSE tick 버퍼)
let streamMeta = { mode: null, source: '' };

// 지표 값 포맷 (그룹/단위별)
function fmtValue(ind) {
    const v = ind.value;
    if (ind.unit === '%') return v.toFixed(2) + '<span class="unit">%</span>';
    if (ind.unit === '$') return '<span class="unit">$</span>' + v.toFixed(1);
    if (ind.unit === 'pt') return v.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
    return '<span class="unit">₩</span>' + Math.round(v).toLocaleString('ko-KR');
}

// 변동폭 절대값 포맷 (표시 정밀도에서 0이면 부호 생략)
function fmtChange(ind, change) {
    const a = Math.abs(change);
    let mag;
    if (ind.unit === '%') mag = a.toFixed(2);                        // %p
    else if (ind.unit === '$') mag = '$' + a.toFixed(1);
    else mag = Math.round(a).toLocaleString('ko-KR');
    const isZero = parseFloat(mag.replace(/[^0-9.]/g, '')) === 0;
    if (isZero) return mag;
    return (change >= 0 ? '+' : '−') + mag;
}

const SPARK_W = 220, SPARK_H = 48, SPARK_PAD = 3;

// 값 포맷 (세로축/최대·최소 라벨용)
function fmtNum(v, unit) {
    if (unit === '%') return v.toFixed(2);
    if (unit === '$') return '$' + v.toFixed(1);
    return Math.round(v).toLocaleString('ko-KR');
}

// 스파크라인 SVG 생성 (선 + 영역만; 점/라벨은 HTML 오버레이)
function sparkline(series, up, scaleMin, scaleMax) {
    const W = SPARK_W, H = SPARK_H, pad = SPARK_PAD;
    const min = Number.isFinite(scaleMin) ? scaleMin : Math.min(...series);
    const max = Number.isFinite(scaleMax) ? scaleMax : Math.max(...series);
    const range = (max - min) || 1;
    const n = series.length;
    const den = Math.max(1, n - 1);
    const x = (i) => pad + (i / den) * (W - pad * 2);
    const y = (val) => H - pad - ((val - min) / range) * (H - pad * 2);
    const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const color = up ? 'var(--positive)' : 'var(--negative)';
    const area = `${x(0)},${H} ${pts} ${x(n - 1)},${H}`;
    const gid = 'g' + Math.random().toString(36).slice(2, 8);
    return `
        <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
            <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
                <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
            </linearGradient></defs>
            <polygon points="${area}" fill="url(#${gid})"/>
            <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.0"
                vector-effect="non-scaling-stroke"
                stroke-linejoin="round" stroke-linecap="round"/>
        </svg>`;
}

// 정규장(KST) 시작/종료 — 통화로 시장 구분 (미국=USD, 국내=KRW)
function regularSessionTargets(cur) {
    if (cur === 'KRW') return [{ label: '09:00', kind: 'open' }, { label: '15:30', kind: 'close' }];
    return [{ label: '22:30', kind: 'open' }, { label: '05:00', kind: 'close' }];
}
function hmToMin(s) {
    const m = /^(\d{1,2}):(\d{2})/.exec(s || '');
    return m ? (+m[1]) * 60 + (+m[2]) : null;
}
// times(HH:MM 문자열 배열)에서 target 시각에 가장 가까운 봉 인덱스. 창 밖이면 -1.
function nearestTimeIndex(times, targetMin, tol = 35) {
    let best = -1, bestD = Infinity;
    for (let i = 0; i < times.length; i++) {
        const t = hmToMin(times[i]);
        if (t == null) continue;
        let d = Math.abs(t - targetMin);
        d = Math.min(d, 1440 - d);   // 자정 넘어가는 경우 보정
        if (d < bestD) { bestD = d; best = i; }
    }
    return bestD <= tol ? best : -1;
}
// 정규장 시작/종료 세로 표기용 마크([{idx,label,kind}]). 해당 시각이 창 안에 있을 때만.
function sessionMarks(times, cur) {
    if (!Array.isArray(times) || !times.length) return [];
    return regularSessionTargets(cur).map(s => {
        const idx = nearestTimeIndex(times, hmToMin(s.label));
        return idx >= 0 ? { idx, label: s.label, kind: s.kind } : null;
    }).filter(Boolean);
}

// 차트 블록: 스파크라인 + 세로축(최대/최소) 라벨 + 최고/최저 지점 점 + 시간축
// big=true 면 상세차트(큰 높이 + 축 라벨 5개)
// refLines: [{value, label, kind:'avg'|'target'|'stop'}] (단일 숫자도 평단으로 허용)
// vmarks: [{idx, label, kind:'open'|'close'}] 정규장 시작/종료 세로 점선(장중 차트 전용)
function chartBlock(series, times, up, unit, big, refLines, vmarks) {
    if (!Array.isArray(series) || !series.length) series = [0];
    const sMin = Math.min(...series), sMax = Math.max(...series);  // 시계열 실제 최소/최대
    let lines = [];
    if (Number.isFinite(refLines)) lines = [{ value: refLines, label: '평단', kind: 'avg' }];
    else if (Array.isArray(refLines)) lines = refLines.filter(l => l && Number.isFinite(l.value));
    // 기준선이 범위 밖이면 스케일을 확장해 선이 보이게
    let min = sMin, max = sMax;
    lines.forEach(l => { min = Math.min(min, l.value); max = Math.max(max, l.value); });
    const range = (max - min) || 1;
    const n = series.length, den = Math.max(1, n - 1);
    const W = SPARK_W, H = SPARK_H, pad = SPARK_PAD;
    // SVG 좌표를 %로 변환(오버레이 점 위치가 선과 일치)
    const leftPct = (i) => ((pad + (i / den) * (W - pad * 2)) / W) * 100;
    const topPct = (v) => ((H - pad - ((v - min) / range) * (H - pad * 2)) / H) * 100;
    const iMax = series.indexOf(sMax), iMin = series.indexOf(sMin);
    const dot = (i, v, kind) =>
        `<span class="spark-dot ${kind}" style="left:${leftPct(i).toFixed(1)}%;top:${topPct(v).toFixed(1)}%"></span>`;
    const wrapCls = big ? 'spark-wrap spark-wrap--lg' : 'spark-wrap';
    // 기준선(평단/목표/손절) + 라벨
    const refHtml = lines.map(l => {
        const t = topPct(l.value).toFixed(1);
        return `<span class="spark-refline ref-${l.kind}" style="top:${t}%"></span>`
            + `<span class="spark-reflabel ref-${l.kind}" style="top:${t}%">${l.label} ${fmtNum(l.value, unit)}</span>`;
    }).join('');
    // 정규장 시작/종료 세로 점선 + 시각 라벨
    const vHtml = (Array.isArray(vmarks) ? vmarks : []).map(m => {
        const x = leftPct(m.idx).toFixed(1);
        return `<span class="spark-vline sess-${m.kind}" style="left:${x}%"></span>`
            + `<span class="spark-vlabel sess-${m.kind}" style="left:${x}%">${m.label}</span>`;
    }).join('');
    return `
        <div class="${wrapCls}">
            ${sparkline(series, up, min, max)}
            ${vHtml}
            ${refHtml}
            ${dot(iMax, sMax, 'max')}
            ${dot(iMin, sMin, 'min')}
            <span class="spark-y spark-y-max">${fmtNum(sMax, unit)}<span class="spark-y-diff" title="최대값 − 최소값">Δ${fmtNum(sMax - sMin, unit)}</span></span>
            <span class="spark-y spark-y-min">${fmtNum(sMin, unit)}</span>
        </div>
        <div class="spark-axis">${axisLabels(times, big ? 5 : 3)}</div>`;
}

function renderMarket() {
    if (!marketData) return;
    applyBufferedLive();
    const container = document.getElementById('market-groups');
    if (container) {
    container.innerHTML = '';

    marketData.groups.forEach(group => {
        const inds = marketData.indicators.filter(i => i.group === group);
        if (!inds.length) return;

        const groupEl = document.createElement('div');
        groupEl.className = 'market-group';
        groupEl.innerHTML = `<h4 class="group-label">${group}</h4>`;

        const cards = document.createElement('div');
        cards.className = 'market-cards';

        inds.forEach(ind => {
            const p = ind.periods[currentPeriod];
            const up = p.change >= 0;
            const cls = up ? 'positive' : 'negative';
            const arrow = up ? '▲' : '▼';
            const isKis = KIS_KEYS.has(ind.key) && streamMeta.mode === 'live';
            const srcTag = isKis
                ? `<span class="mc-src kis">KIS 실시간</span>`
                : `<span class="mc-src">Yahoo</span>`;
            const card = document.createElement('div');
            card.className = 'market-card';
            card.innerHTML = `
                <div class="mc-top">
                    <span class="mc-name">${ind.name}</span>
                    ${srcTag}
                </div>
                <div class="mc-value">${fmtValue(ind)}</div>
                <div class="mc-change ${cls}">
                    ${arrow} ${fmtChange(ind, p.change)}
                    <span class="mc-pct">(${up ? '+' : ''}${p.change_pct.toFixed(2)}%)</span>
                </div>
                ${chartBlock(p.series, p.times, up, ind.unit)}
            `;
            cards.appendChild(card);
        });

        groupEl.appendChild(cards);
        container.appendChild(groupEl);
    });
    }  // end if(container)

    // 출처 표기: 실시간 브로커(live)면 하이브리드, 아니면 전부 Yahoo
    const live = streamMeta.mode === 'live';
    const mu = document.getElementById('market-updated');
    if (mu) mu.textContent =
        (marketData.updated_at ? '기준: ' + marketData.updated_at.replace('T', ' ') + ' · ' : '')
        + (live ? `금리 Yahoo · 유가/환율 ${streamMeta.source}` : 'Yahoo · 약 15분 지연 · 60초 갱신');

    // 표시등: 실시간 브로커면 '실시간', 아니면 Yahoo 자동갱신(데이터 로드 시 초록)
    const dot = document.getElementById('live-dot');
    if (dot) {
        const on = live || !!marketData;
        dot.className = 'live-dot ' + (on ? 'on' : 'off');
        dot.title = live
            ? `실시간 스트림 수신 중 — ${streamMeta.source}`
            : 'Yahoo 자동 갱신 (약 15분 지연, 60초마다)';
    }

    renderMarketCharts();  // 지표 그래프 패널

    // USD→KRW 환율 갱신 후 계좌 원화 병기 업데이트
    const u = marketData.indicators.find(i => i.key === 'usdkrw');
    if (u && u.value) { usdKrwRate = u.value; updateKrwLines(); }
}

// 지표 그래프: 시장지표 큰 그래프 (지표 그래프 패널)
function renderMarketCharts() {
    const grid = document.getElementById('market-charts-grid');
    if (!grid || !marketData) return;
    grid.innerHTML = '';
    marketData.indicators.forEach(ind => {
        const p = ind.periods[currentPeriod];
        const up = p.change >= 0;
        const cls = up ? 'positive' : 'negative';
        const card = document.createElement('div');
        card.className = 'mchart-card';
        card.innerHTML = `
            <div class="mchart-top">
                <span class="mchart-name">${ind.name}</span>
                <span class="mchart-val">${fmtValue(ind)}
                    <span class="mchart-chg ${cls}">${up ? '▲' : '▼'} ${p.change_pct.toFixed(2)}%</span>
                </span>
            </div>
            ${chartBlock(p.series, p.times, up, ind.unit, true)}
        `;
        grid.appendChild(card);
    });
}

// 섹터별 추이 페이지: SPDR 섹터 ETF 11개. 선택 기간의 등락률 내림차순(1위→꼴찌) 정렬.
function renderSectorCharts() {
    const grid = document.getElementById('sector-grid');
    if (!grid) return;
    if (!sectorData) {
        grid.innerHTML = '<div class="loading-row">섹터 그래프를 불러오는 중...</div>';
        return;
    }
    grid.innerHTML = '';
    const rows = sectorData.sectors
        .map(s => ({ s, p: s.periods[sectorPeriod] }))
        .filter(r => r.p)
        .sort((a, b) => b.p.change_pct - a.p.change_pct);
    rows.forEach((r, i) => {
        const { s, p } = r;
        const up = p.change_pct >= 0;
        const cls = up ? 'positive' : 'negative';
        const card = document.createElement('div');
        card.className = 'mchart-card sector-card';
        card.innerHTML = `
            <div class="mchart-top">
                <span class="mchart-name"><span class="sec-rank ${cls}">${i + 1}</span> ${s.name}</span>
                <span class="mchart-val mchart-chg ${cls}">${up ? '▲' : '▼'} ${up ? '+' : ''}${p.change_pct.toFixed(2)}%</span>
            </div>
            ${chartBlock(p.series, p.times, up, s.unit, true)}
            <button class="sec-expand" data-key="${s.key}">구성종목 <i class="fa-solid fa-chevron-down"></i></button>
            <div class="sector-members" data-key="${s.key}" hidden></div>
        `;
        grid.appendChild(card);
    });
    // 펼쳐둔 섹터는 다시 펼침(기간 바뀌면 해당 기간으로 재조회)
    expandedSectors.forEach(k => expandSector(k));
    const su = document.getElementById('sector-updated');
    if (su) su.textContent =
        (sectorData.updated_at ? '기준: ' + sectorData.updated_at.replace('T', ' ') + ' · ' : '')
        + 'SPDR 섹터 ETF · Yahoo (약 15분 지연)';
}

// 구성종목 미니 카드 목록 HTML
function renderMembers(members) {
    if (!members || !members.length) return '<div class="sec-mem-empty">구성종목 정보 없음</div>';
    return members.map(m => {
        const up = (m.change_pct || 0) >= 0;
        const cls = up ? 'positive' : 'negative';
        return `
            <div class="sec-mem ${m.held ? 'held' : ''}">
                <div class="sec-mem-top">
                    <span class="sec-mem-code">${m.code}${m.held ? '<span class="sec-mem-badge" title="보유 종목">보유</span>' : ''}</span>
                    <span class="sec-mem-chg ${cls}">${up ? '▲' : '▼'} ${up ? '+' : ''}${(m.change_pct || 0).toFixed(2)}%</span>
                </div>
                ${m.industry ? `<div class="sec-mem-ind">${m.industry}</div>` : ''}
                <div class="sec-mem-spark">${sparkline(m.series && m.series.length ? m.series : [0], up)}</div>
            </div>`;
    }).join('');
}

// 섹터 펼치기: 현재 기간(sectorPeriod)으로 구성종목 조회/렌더
async function expandSector(key) {
    expandedSectors.add(key);
    const btn = document.querySelector(`.sec-expand[data-key="${key}"]`);
    const box = document.querySelector(`.sector-members[data-key="${key}"]`);
    if (!box) return;
    box.hidden = false;
    if (btn) btn.innerHTML = '구성종목 접기 <i class="fa-solid fa-chevron-up"></i>';
    const ck = key + '|' + sectorPeriod;
    if (memberCache[ck]) { box.innerHTML = renderMembers(memberCache[ck]); return; }
    box.innerHTML = '<div class="loading-row sec-mem-loading">구성종목 불러오는 중...</div>';
    try {
        const d = await fetchJSON(`/api/sectors/${key}/members?period=${sectorPeriod}`);
        memberCache[ck] = d.members;
        const b2 = document.querySelector(`.sector-members[data-key="${key}"]`);
        if (b2 && expandedSectors.has(key)) b2.innerHTML = renderMembers(d.members);
    } catch (err) {
        console.error(err);
        const b2 = document.querySelector(`.sector-members[data-key="${key}"]`);
        if (b2) b2.innerHTML = '<div class="loading-row">구성종목을 불러올 수 없습니다.</div>';
    }
}

function collapseSector(key) {
    expandedSectors.delete(key);
    const btn = document.querySelector(`.sec-expand[data-key="${key}"]`);
    const box = document.querySelector(`.sector-members[data-key="${key}"]`);
    if (box) { box.hidden = true; box.innerHTML = ''; }
    if (btn) btn.innerHTML = '구성종목 <i class="fa-solid fa-chevron-down"></i>';
}

// 섹터 데이터 로드(필요 시) 후 현재 보기 렌더
async function loadSectors(force) {
    if (sectorData && !force) { renderSectorView(); return; }
    try {
        sectorData = await fetchJSON('/api/sectors');
        sectorLoaded = true;
    } catch (err) {
        console.error(err);
        const box = document.getElementById(sectorMode === 'momentum' ? 'sector-momentum' : 'sector-grid');
        if (box) box.innerHTML = '<div class="loading-row">섹터 데이터를 불러올 수 없습니다.</div>';
        return;
    }
    renderSectorView();
}

// 보기(모멘텀/그래프)에 맞게 렌더
function renderSectorView() {
    const mom = document.getElementById('sector-momentum');
    const grid = document.getElementById('sector-grid');
    const pt = document.getElementById('sector-period-toggle');
    const isMom = sectorMode === 'momentum';
    if (mom) mom.hidden = !isMom;
    if (grid) grid.hidden = isMom;
    if (pt) pt.hidden = isMom;  // 기간 토글은 그래프 보기에서만
    if (isMom) renderSectorMomentum(); else renderSectorCharts();
}

// 섹터 모멘텀 랭킹 보드 (갈아타기 판단)
function renderSectorMomentum() {
    const box = document.getElementById('sector-momentum');
    if (!box) return;
    if (!sectorData) { box.innerHTML = '<div class="loading-row">모멘텀을 불러오는 중...</div>'; return; }
    const rows = sectorData.sectors.map(s => {
        const r1w = s.periods['1W']?.change_pct ?? 0;
        const r1m = s.periods['1M']?.change_pct ?? 0;
        const r3m = s.periods['3M']?.change_pct ?? 0;
        const m = momCalc(r1w, r1m, r3m);
        return { s, r1w, r1m, r3m, ...m };
    }).sort((a, b) => b.score - a.score);

    let html = `
        <p class="mom-guide"><i class="fa-solid fa-circle-info"></i>
            세 기간 수익률과 <b>가속</b>(최근 1주 속도 − 1개월 속도)으로 순위. ● = 내 보유 섹터 · 섹터를 누르면 종목 모멘텀·갈아타기 후보. <span class="mom-dis">참고용(투자 자문 아님)</span></p>
        <table class="mom-table">
            <thead><tr>
                <th>#</th><th>섹터</th>
                <th class="num">1주</th><th class="num">1개월</th><th class="num">3개월</th>
                <th class="num">가속</th><th></th>
            </tr></thead>
            <tbody>`;
    rows.forEach((r, i) => {
        const a = accelLabel(r.accel);
        const held = HELD_SECTORS.has(r.s.key);
        const open = expandedMom.has(r.s.key);
        html += `
            <tr class="mom-row ${open ? 'open' : ''}" data-key="${r.s.key}">
                <td class="mom-rank">${i + 1}</td>
                <td class="mom-name">${held ? '<span class="mom-hold">●</span> ' : ''}${r.s.name}</td>
                ${pctCell(r.r1w)}${pctCell(r.r1m)}${pctCell(r.r3m)}
                <td class="num"><span class="accel ${a.cls}">${a.txt}</span></td>
                <td class="mom-exp"><i class="fa-solid fa-chevron-${open ? 'up' : 'down'}"></i></td>
            </tr>
            <tr class="mom-detail-row" data-key="${r.s.key}" ${open ? '' : 'hidden'}>
                <td colspan="7"><div class="mom-detail" data-key="${r.s.key}"></div></td>
            </tr>`;
    });
    html += `</tbody></table>`;
    box.innerHTML = html;
    expandedMom.forEach(k => expandMomSector(k));
    const su = document.getElementById('sector-updated');
    if (su) su.textContent =
        (sectorData.updated_at ? '기준: ' + sectorData.updated_at.replace('T', ' ') + ' · ' : '')
        + 'Yahoo (약 15분 지연)';
}

// 섹터 펼침: 구성종목 모멘텀 표(점수순) + 같은 섹터 갈아타기 후보 표시
async function expandMomSector(key) {
    expandedMom.add(key);
    const row = document.querySelector(`.mom-row[data-key="${key}"]`);
    const dr = document.querySelector(`.mom-detail-row[data-key="${key}"]`);
    const box = document.querySelector(`.mom-detail[data-key="${key}"]`);
    if (row) row.classList.add('open');
    if (dr) dr.hidden = false;
    const chev = row && row.querySelector('.mom-exp i');
    if (chev) chev.className = 'fa-solid fa-chevron-up';
    if (!box) return;
    if (momCache[key]) { box.innerHTML = renderMomMembers(momCache[key]); return; }
    box.innerHTML = '<div class="loading-row">종목 모멘텀 계산 중...</div>';
    try {
        const d = await fetchJSON(`/api/sectors/${key}/momentum`);
        momCache[key] = d;
        const b2 = document.querySelector(`.mom-detail[data-key="${key}"]`);
        if (b2 && expandedMom.has(key)) b2.innerHTML = renderMomMembers(d);
    } catch (err) {
        console.error(err);
        const b2 = document.querySelector(`.mom-detail[data-key="${key}"]`);
        if (b2) b2.innerHTML = '<div class="loading-row">종목 모멘텀을 불러올 수 없습니다.</div>';
    }
}

function collapseMomSector(key) {
    expandedMom.delete(key);
    const row = document.querySelector(`.mom-row[data-key="${key}"]`);
    const dr = document.querySelector(`.mom-detail-row[data-key="${key}"]`);
    if (row) row.classList.remove('open');
    if (dr) { dr.hidden = true; }
    const box = document.querySelector(`.mom-detail[data-key="${key}"]`);
    if (box) box.innerHTML = '';
    const chev = row && row.querySelector('.mom-exp i');
    if (chev) chev.className = 'fa-solid fa-chevron-down';
}

// 구성종목 모멘텀 표. 갈아타기 후보 = 내 보유보다 순위 높은 비보유 종목.
function renderMomMembers(d) {
    const members = (d && d.members) || [];
    const industries = (d && d.industries) || [];
    if (!members.length) return '<div class="loading-row">구성종목 없음</div>';
    // 보유 종목 중 가장 낮은 순위(rank 큰 값) → 그보다 위의 비보유는 '후보'
    const heldRanks = members.filter(m => m.held).map(m => m.rank);
    const worstHeld = heldRanks.length ? Math.max(...heldRanks) : 0;
    const anyHeld = heldRanks.length > 0;

    // 산업별 요약(강한 하위 산업 순)
    let indHtml = '';
    if (industries.length) {
        indHtml = '<div class="mom-ind-row"><span class="mom-ind-lbl">산업별</span>' +
            industries.map(g => {
                const a = accelLabel(g.accel);
                const up = (g.r1w || 0) >= 0;
                return `<span class="mom-ind-chip">
                    <b>${g.held ? '<span class="mom-hold">●</span> ' : ''}${g.name}</b>
                    <span class="mom-ind-n">${g.count}</span>
                    <span class="${up ? 'positive' : 'negative'}">1주 ${up ? '+' : ''}${(g.r1w || 0).toFixed(1)}%</span>
                    <span class="accel ${a.cls}">${a.txt}</span>
                </span>`;
            }).join('') + '</div>';
    }

    let html = indHtml + `
        <div class="mom-mem-legend">종목 모멘텀 점수순 · <span class="mom-hold">●</span> 보유 · <span class="cand-badge">후보</span> 같은 섹터에서 보유보다 센 종목</div>
        <table class="mom-table mom-mem-table">
            <thead><tr>
                <th>#</th><th>종목</th><th>산업</th><th class="num">현재가</th>
                <th class="num">1주</th><th class="num">1개월</th><th class="num">3개월</th>
                <th class="num">가속</th><th>추이(3M)</th>
            </tr></thead><tbody>`;
    members.forEach(m => {
        if (m.no_data) {
            html += `<tr class="${m.held ? 'held' : ''}"><td>${m.rank}</td><td class="mom-name">${m.code}</td><td class="mom-ind-tag">${m.industry || ''}</td><td colspan="6" class="mom-nodata">데이터 없음</td></tr>`;
            return;
        }
        const a = accelLabel(m.accel);
        const up = (m.r1w || 0) >= 0;
        const cand = !m.held && anyHeld && m.rank < worstHeld;
        html += `
            <tr class="${m.held ? 'held' : ''} ${cand ? 'cand' : ''}">
                <td>${m.rank}</td>
                <td class="mom-name">${m.held ? '<span class="mom-hold">●</span> ' : ''}${m.code}${cand ? '<span class="cand-badge">후보</span>' : ''}</td>
                <td class="mom-ind-tag">${m.industry || ''}</td>
                <td class="num">$${(m.value || 0).toFixed(2)}</td>
                ${pctCell(m.r1w)}${pctCell(m.r1m)}${pctCell(m.r3m)}
                <td class="num"><span class="accel ${a.cls}">${a.txt}</span></td>
                <td class="mom-spark">${sparkline(m.series && m.series.length ? m.series : [0], up)}</td>
            </tr>`;
    });
    html += `</tbody></table>`;
    return html;
}

// 좌측 메뉴 보기 전환 (대시보드 ↔ 섹터 추이)
const DASH_SELECTORS = ['.market-charts-section', '.kis-section', '.holdings-charts', '.dashboard-grid'];
function showView(name) {
    currentView = name;
    const isDash = name === 'dashboard';
    DASH_SELECTORS.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => { el.hidden = !isDash; });
    });
    const sp = document.getElementById('sector-page');
    if (sp) sp.hidden = name !== 'sector';
    const lp = document.getElementById('ledger-page');
    if (lp) lp.hidden = name !== 'ledger';
    const pp = document.getElementById('portfolio-page');
    if (pp) pp.hidden = name !== 'portfolio';
    const main = document.querySelector('.main-content');
    if (main) {
        main.classList.toggle('mode-sector', name === 'sector');
        main.classList.toggle('mode-ledger', name === 'ledger');
        main.classList.toggle('mode-portfolio', name === 'portfolio');
    }
    // 사이드바 활성 표시
    document.querySelectorAll('.sidebar nav li').forEach(li => {
        const a = li.querySelector('a');
        li.classList.toggle('active', !!a && a.dataset.view === name);
    });
    if (name === 'sector') loadSectors(!sectorLoaded);
    if (name === 'ledger') loadLedger();
    if (name === 'portfolio') loadPnl();
}

function initSidebarNav() {
    document.querySelectorAll('.sidebar nav a[data-view]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            showView(a.dataset.view);
        });
    });
}

// ── 입출금·매매 내역(원장) ─────────────────────────────────────────────── //
let ledgerData = [];
let ledgerFilter = { currency: 'all', type: 'all' };
let ledgerLoaded = false;
let ledgerInited = false;

// 거래구분명(smcd_name)으로 유형 분류 (tr_type_code는 05=RP매수·주식매수 혼용이라 못 씀)
function txCategory(name) {
    const s = name || '';
    if (/주식.*(매수|매도)/.test(s)) return 'trade';                 // 주식매수/매도, 해외주식 매수/매도
    if (/환전/.test(s)) return 'fx';
    if (/(입금|출금|이체)/.test(s) && !/주식/.test(s)) return 'cash'; // 전자계좌입출금, 외화이체입금
    return 'etc';                                                    // 대체입고(이관)·RP·이용료 등
}

function _fmtInt(v) { return Math.round(Number(v) || 0).toLocaleString('ko-KR'); }
function _fmtUsd(v) { return (Number(v) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function _fmtDate8(s) { return (s && s.length === 8) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : (s || '-'); }

async function loadLedger(force) {
    initLedgerControls();
    if (ledgerLoaded && !force) { renderLedger(); return; }
    const tbody = document.getElementById('ledger-tbody');
    try {
        const d = await fetch('/api/ledger/transactions?limit=1000').then(r => r.json());
        ledgerData = (d.transactions || []).map(r => ({ ...r, _cat: txCategory(r.smcd_name) }));
        ledgerLoaded = true;
        renderLedgerSummary(d.stats, d.total);
        renderLedger();
        const u = document.getElementById('ledger-updated');
        if (u) u.textContent = '기준: ' + new Date().toLocaleString('ko-KR', { hour12: false });
    } catch (e) {
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="loading-row">불러올 수 없습니다.</td></tr>';
    }
}

function renderLedgerSummary(st, total) {
    const el = document.getElementById('ledger-summary');
    if (!el || !st) return;
    el.innerHTML =
        `<div class="lsum-card"><div class="lsum-label">총 건수</div><div class="lsum-val">${total ?? st.rows ?? 0}</div></div>`
        + `<div class="lsum-card"><div class="lsum-label">기간</div><div class="lsum-val sm">${_fmtDate8(st.min_date)} ~ ${_fmtDate8(st.max_date)}</div></div>`;
}

function ledgerRow(r) {
    const inc = r.io_cls_code === '1';
    const sign = inc ? '+' : '−';
    const cls = inc ? 'pos' : 'neg';
    const mmdd = (r.tr_date && r.tr_date.length === 8) ? `${r.tr_date.slice(4, 6)}-${r.tr_date.slice(6, 8)}` : (r.tr_date || '');
    const qty = Number(r.qty) || 0;
    const qtyCell = qty ? _fmtInt(qty) : '—';
    let amtCell;
    if (r.currency === 'USD' && Number(r.frcr_amt)) {
        amtCell = `<div class="${cls}">${sign}$${_fmtUsd(r.frcr_amt)}</div>`
            + (Number(r.amount) ? `<div class="lg-sub">₩${_fmtInt(r.amount)}</div>` : '');
    } else if (Number(r.amount)) {
        amtCell = `<div class="${cls}">${sign}₩${_fmtInt(r.amount)}</div>`;
    } else if (Number(r.byng_unpr)) {
        amtCell = `<div class="lg-sub">@${_fmtInt(r.byng_unpr)}</div>`;
    } else {
        amtCell = '<span class="lg-sub">—</span>';
    }
    const cur = r.currency === 'USD' ? '$' : '₩';
    const det = [];
    if (Number(r.fee)) det.push(`수수료 ${cur}${_fmtInt(r.fee)}`);
    if (Number(r.tax)) det.push(`세금 ₩${_fmtInt(r.tax)}`);
    if (Number(r.byng_unpr)) det.push(`매입단가 ${_fmtInt(r.byng_unpr)}`);
    det.push(`거래코드 ${r.tr_type_code || '-'}`);
    det.push(`#${r.srno}`);
    const badge = r.source === 'exec' ? '<span class="lg-badge exec">체결</span>' : '';
    return `<tr class="lg-row" data-srno="${r.srno}">
        <td class="lg-date">${mmdd}</td>
        <td class="lg-kind">${r.smcd_name || '-'}${badge}</td>
        <td class="lg-name">${r.name || '—'}</td>
        <td class="num">${qtyCell}</td>
        <td class="num lg-amt">${amtCell}</td>
        <td class="c lg-cur">${r.currency || ''}</td>
    </tr>
    <tr class="lg-detail" data-srno="${r.srno}" hidden><td colspan="6">${det.join(' · ')}</td></tr>`;
}

function renderLedger() {
    const tbody = document.getElementById('ledger-tbody');
    if (!tbody) return;
    const f = ledgerFilter;
    const rows = ledgerData.filter(r =>
        (f.currency === 'all' || r.currency === f.currency) &&
        (f.type === 'all' || r._cat === f.type));
    tbody.innerHTML = rows.length
        ? rows.map(ledgerRow).join('')
        : '<tr><td colspan="6" class="loading-row">해당 거래가 없습니다.</td></tr>';
}

function initLedgerControls() {
    if (ledgerInited) return;
    ledgerInited = true;
    const cur = document.getElementById('lf-currency');
    const typ = document.getElementById('lf-type');
    if (cur) cur.addEventListener('click', e => {
        const b = e.target.closest('button'); if (!b) return;
        cur.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active'); ledgerFilter.currency = b.dataset.cur; renderLedger();
    });
    if (typ) typ.addEventListener('click', e => {
        const b = e.target.closest('button'); if (!b) return;
        typ.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active'); ledgerFilter.type = b.dataset.type; renderLedger();
    });
    const tbody = document.getElementById('ledger-tbody');
    if (tbody) tbody.addEventListener('click', e => {
        const row = e.target.closest('tr.lg-row'); if (!row) return;
        const det = row.nextElementSibling;
        if (det && det.classList.contains('lg-detail')) {
            det.hidden = !det.hidden;
            row.classList.toggle('open', !det.hidden);
        }
    });
    const sync = document.getElementById('ledger-sync');
    if (sync) sync.addEventListener('click', async () => {
        sync.disabled = true; sync.classList.add('busy');
        try { await fetch('/api/ledger/sync', { method: 'POST' }); await loadLedger(true); }
        catch (e) { /* noop */ }
        finally { sync.disabled = false; sync.classList.remove('busy'); }
    });
}

// ── 포트폴리오 손익(실현+미실현=총수익) ──────────────────────────────────── //
function _sc(v) { return (Number(v) || 0) >= 0 ? 'pos' : 'neg'; }
function _pm(v, cur) {   // 금액(부호 없음)
    const n = Number(v) || 0;
    return cur === 'USD'
        ? '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : '₩' + Math.round(n).toLocaleString('ko-KR');
}
function _ps(v, cur) {   // 금액(부호 포함)
    const n = Number(v) || 0;
    return (n >= 0 ? '+' : '−') + _pm(Math.abs(n), cur);
}
function _pp(v) {
    if (v === null || v === undefined || v === '') return '';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function pnlRow(s) {
    const cur = s.currency;
    const soldBadge = s.held ? '' : '<span class="lg-badge sold">매도완료</span>';
    const unrealCell = s.held
        ? `<div class="${_sc(s.unreal)}">${_ps(s.unreal, cur)}</div>`
          + (s.unreal_pct != null ? `<div class="lg-sub">${_pp(s.unreal_pct)}</div>` : '')
        : '<span class="lg-sub">—</span>';
    const realCell = Number(s.real)
        ? `<div class="${_sc(s.real)}">${_ps(s.real, cur)}</div>`
          + (s.real_pct != null ? `<div class="lg-sub">${_pp(s.real_pct)}</div>` : '')
        : '<span class="lg-sub">—</span>';
    const totalCell = `<div class="${_sc(s.total_krw)}">${_ps(s.total_krw, 'KRW')}</div>`
        + (cur === 'USD' ? `<div class="lg-sub">${_ps(s.total, cur)}</div>` : '');
    const hasDetail = (s.real_detail || []).length > 0;
    const chev = hasDetail ? '<i class="fa-solid fa-chevron-right pnl-chev"></i> ' : '';
    const detailRow = hasDetail
        ? `<tr class="pnl-detail" data-key="${s.key}" hidden><td colspan="8">${pnlDetail(s)}</td></tr>`
        : '';
    return `<tr class="pnl-row ${hasDetail ? 'has-detail' : ''}" data-key="${s.key}">
        <td class="lg-name">${chev}${s.name || s.code || ''}${soldBadge}</td>
        <td class="num">${s.qty ? _fmtInt(s.qty) : '—'}</td>
        <td class="num">${s.held ? _pm(s.avg, cur) : '—'}</td>
        <td class="num">${s.held ? _pm(s.price, cur) : '—'}</td>
        <td class="num">${unrealCell}</td>
        <td class="num">${realCell}</td>
        <td class="num">${totalCell}</td>
        <td class="c">${cur}</td>
    </tr>${detailRow}`;
}

function pnlDetail(s) {
    const d = s.real_detail || [];
    if (!d.length) return '';
    if (d[0].kind === 'summary') {
        const x = d[0];
        return `<div class="pd-summary">매수원가 ${_pm(x.buy_amt, 'KRW')} · 매도금액 ${_pm(x.sell_amt, 'KRW')} `
            + `· 세금 ${_pm(x.tax, 'KRW')} · 실현 <span class="${_sc(x.rlzt)}">${_ps(x.rlzt, 'KRW')}</span></div>`;
    }
    const rows = d.map(x => {
        const md = x.date && x.date.length === 8 ? `${x.date.slice(4, 6)}-${x.date.slice(6, 8)}` : (x.date || '');
        return `<tr>
            <td>${md}</td>
            <td class="num">${_fmtInt(x.qty)}</td>
            <td class="num">$${(Number(x.sell_px) || 0).toFixed(2)}</td>
            <td class="num">$${(Number(x.avg_px) || 0).toFixed(2)}</td>
            <td class="num ${_sc(x.deal_pl)}">${_ps(x.deal_pl, 'KRW')}</td>
            <td class="num ${_sc(x.fx_pl)}">${_ps(x.fx_pl, 'KRW')}</td>
            <td class="num">$${(Number(x.fee) || 0).toFixed(2)}</td>
            <td class="num ${_sc(x.rlzt)}">${_ps(x.rlzt, 'KRW')}</td>
        </tr>`;
    }).join('');
    return `<table class="pd-table"><thead><tr>
        <th>매도일</th><th class="num">수량</th><th class="num">매도가</th><th class="num">평단</th>
        <th class="num">매매손익</th><th class="num">환차손익</th><th class="num">수수료</th><th class="num">실현</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

let pnlInited = false;
function initPnlControls() {
    if (pnlInited) return;
    pnlInited = true;
    const tbody = document.getElementById('pnl-tbody');
    if (tbody) tbody.addEventListener('click', e => {
        const row = e.target.closest('tr.pnl-row');
        if (!row || !row.classList.contains('has-detail')) return;
        const det = row.nextElementSibling;
        if (det && det.classList.contains('pnl-detail')) {
            det.hidden = !det.hidden;
            row.classList.toggle('open', !det.hidden);
        }
    });
}

function renderPnlSummary(d) {
    const el = document.getElementById('pnl-summary');
    if (!el) return;
    const t = d.summary || {};
    el.innerHTML =
        `<div class="lsum-card big"><div class="lsum-label">총수익 (실현 + 미실현)</div>`
        + `<div class="lsum-val ${_sc(t.total_krw)}">${_ps(t.total_krw, 'KRW')}</div>`
        + `<div class="lsum-note">개설시점 누적 · 고시환율 ₩${_fmtInt(d.fx)} · ${d.as_of || ''}</div></div>`;
}

async function loadPnl() {
    initPnlControls();
    const tbody = document.getElementById('pnl-tbody');
    try {
        const d = await fetch('/api/ledger/pnl?_=' + Date.now()).then(r => r.json());
        renderPnlSummary(d);
        const syms = d.symbols || [];
        if (tbody) tbody.innerHTML = syms.length
            ? syms.map(pnlRow).join('')
            : '<tr><td colspan="8" class="loading-row">데이터가 없습니다.</td></tr>';
        const t = d.summary || {};
        const tf = document.getElementById('pnl-tfoot');
        if (tf) tf.innerHTML = `<tr class="pnl-total-row">
            <td>합계</td><td class="num"></td><td class="num"></td><td class="num"></td>
            <td class="num ${_sc(t.unreal_krw)}">${_ps(t.unreal_krw, 'KRW')}</td>
            <td class="num ${_sc(t.real_krw)}">${_ps(t.real_krw, 'KRW')}</td>
            <td class="num ${_sc(t.total_krw)}">${_ps(t.total_krw, 'KRW')}</td><td></td></tr>`;
        const u = document.getElementById('pnl-updated');
        if (u) u.textContent = '기준: ' + (d.as_of || '');
    } catch (e) {
        if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="loading-row">불러올 수 없습니다.</td></tr>';
    }
}

function initSectorPeriodToggle() {
    const toggle = document.getElementById('sector-period-toggle');
    if (toggle) {
        toggle.addEventListener('click', (e) => {
            const btn = e.target.closest('button');
            if (!btn) return;
            toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            sectorPeriod = btn.dataset.period;
            renderSectorCharts();  // 펼쳐둔 섹터는 새 기간으로 재조회됨
        });
    }
    // 구성종목 펼치기/접기 (이벤트 위임)
    const grid = document.getElementById('sector-grid');
    if (grid) grid.addEventListener('click', (e) => {
        const b = e.target.closest('.sec-expand');
        if (!b) return;
        const k = b.dataset.key;
        if (expandedSectors.has(k)) collapseSector(k); else expandSector(k);
    });

    // 모멘텀/그래프 보기 전환
    const vt = document.getElementById('sector-view-toggle');
    if (vt) vt.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        vt.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        sectorMode = btn.dataset.sview;
        renderSectorView();
    });

    // 모멘텀 섹터 행 펼치기/접기 (이벤트 위임)
    const mom = document.getElementById('sector-momentum');
    if (mom) mom.addEventListener('click', (e) => {
        const r = e.target.closest('.mom-row');
        if (!r) return;
        const k = r.dataset.key;
        if (expandedMom.has(k)) collapseMomSector(k); else expandMomSector(k);
    });
}

// SSE 버퍼(livePrices)의 실시간 값을 marketData 에 반영
function applyBufferedLive() {
    if (!marketData) return;
    for (const ind of marketData.indicators) {
        const live = livePrices[ind.key];
        if (live) applyLiveToIndicator(ind, live.value, live.as_of);
    }
}

// 한 지표에 실시간 값 반영: 헤드라인 값 + 1일 스파크라인/변동/시간축 갱신
function applyLiveToIndicator(ind, value, asOf) {
    ind.value = value;
    if (asOf) ind.as_of = asOf;
    const p1d = ind.periods && ind.periods['1D'];
    if (p1d && Array.isArray(p1d.series) && p1d.series.length) {
        // 축(시각/길이)은 고정하고 마지막 점만 실시간 값으로 갱신 → 가로축이 밀리지 않음
        p1d.series[p1d.series.length - 1] = Math.round(value * 10000) / 10000;
        const start = p1d.series[0];
        p1d.change = Math.round((value - start) * 10000) / 10000;
        p1d.change_pct = start ? Math.round((p1d.change / start * 100) * 100) / 100 : 0;
    }
}

// 시간축 라벨: 처음/중간/끝 3개를 균등 추출
function axisLabels(times, count = 3) {
    if (!Array.isArray(times) || !times.length) return '';
    const pick = (i) => times[Math.min(times.length - 1, Math.max(0, i))] || '';
    const last = times.length - 1;
    const c = Math.min(count, times.length);
    let labels;
    if (c <= 1) labels = [pick(last)];
    else labels = Array.from({ length: c }, (_, k) => pick(Math.round((last * k) / (c - 1))));
    return labels.map(t => `<span>${t}</span>`).join('');
}

// 계좌 요약·보유 표만 다시 불러와 갱신 (자동 새로고침용, 차트/거래내역 제외)
async function refreshAccount() {
    try {
        renderAccount(await fetchJSON('/api/account/status'));
        loadDomestic();
    } catch (err) {
        console.error(err);
    }
}

// 시장 지표만 다시 불러와 갱신 (자동 새로고침용)
async function refreshMarket() {
    try {
        marketData = await fetchJSON('/api/market');
        renderMarket();
    } catch (err) {
        console.error(err);
        const dot = document.getElementById('live-dot');
        if (dot) { dot.className = 'live-dot off'; dot.title = '갱신 실패'; }
    }
}

// --- SSE 실시간 스트림 (KIS 유가/환율) ---------------------------------
let renderScheduled = false;
function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    setTimeout(() => { renderScheduled = false; renderMarket(); }, 200);  // tick 배치
}

function handleTick(msg) {
    if (msg.type === 'meta') {
        streamMeta = { mode: msg.mode, source: msg.source };
        scheduleRender();
        return;
    }
    if (msg.type === 'tick' && msg.key) {
        // 실제 브로커 실시간(live)일 때만 틱으로 덮어씀. 그 외에는 Yahoo 값 유지.
        if (streamMeta.mode !== 'live') return;
        livePrices[msg.key] = { value: msg.value, as_of: msg.as_of };
        if (marketData) {
            const ind = marketData.indicators.find(i => i.key === msg.key);
            if (ind) applyLiveToIndicator(ind, msg.value, msg.as_of);
        }
        scheduleRender();
    }
}

function connectStream() {
    let es;
    try {
        es = new EventSource(API_BASE + '/api/market/stream');
    } catch (err) {
        console.error('SSE 연결 실패', err);
        return;
    }
    es.onmessage = (e) => {
        try { handleTick(JSON.parse(e.data)); } catch (_) { /* ping 등 무시 */ }
    };
    es.onerror = () => {
        // EventSource 는 자동 재연결. 표시등만 대기 상태로.
        const dot = document.getElementById('live-dot');
        if (dot) { dot.className = 'live-dot off'; dot.title = 'KIS 스트림 재연결 중...'; }
    };
}

function initPeriodToggle() {
    const toggle = document.getElementById('period-toggle');
    toggle.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentPeriod = btn.dataset.period;
        renderMarket();
    });
}

// ---------------------------------------------------------------------------
// 최근 거래 내역
// ---------------------------------------------------------------------------
function renderTransactions(transactions) {
    const list = document.getElementById('transaction-list');
    list.innerHTML = '';
    transactions.forEach(tx => {
        const li = document.createElement('li');
        li.className = 'transaction-item';
        const amount = (tx.amount >= 0 ? '+ ' : '- ') +
            Math.abs(tx.amount).toLocaleString('ko-KR');
        li.innerHTML = `
            <div class="tx-left">
                <div class="tx-icon"><i class="fa-solid ${tx.icon}"></i></div>
                <div class="tx-details">
                    <h4>${tx.title}</h4>
                    <p>${tx.category} • ${tx.date}</p>
                </div>
            </div>
            <div class="tx-amount ${tx.type}">${amount} 원</div>
        `;
        list.appendChild(li);
    });
}

// ---------------------------------------------------------------------------
// 로드
// ---------------------------------------------------------------------------
async function loadAll() {
    try {
        const account = await fetchJSON('/api/account/status');
        renderAccount(account);
        loadHoldingsCharts();  // 보유종목 전체 차트
        loadDomestic();        // 국내주식(원화) 섹션
    } catch (err) {
        console.error(err);
        const badge = document.getElementById('mode-badge');
        if (badge) {
            badge.className = 'mode-badge error';
            badge.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> 백엔드 연결 실패';
            badge.title = String(err) + ' — 백엔드(uvicorn)가 실행 중인지 확인하세요.';
        }
        document.getElementById('holdings-body').innerHTML =
            '<tr><td colspan="7" class="loading-row">백엔드에 연결할 수 없습니다. 서버를 실행하세요.</td></tr>';
    }

    try {
        marketData = await fetchJSON('/api/market');
        renderMarket();
    } catch (err) {
        console.error(err);
        document.getElementById('market-groups').innerHTML =
            '<div class="loading-row">시장 지표를 불러올 수 없습니다.</div>';
    }

    try {
        const { transactions } = await fetchJSON('/api/transactions');
        renderTransactions(transactions);
    } catch (err) {
        console.error(err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initPeriodToggle();
    initHoldingToggle();
    initRebalance();
    initSidebarNav();
    initSectorPeriodToggle();
    loadAll();
    connectStream();  // KIS 실시간 tick (SSE)
    document.getElementById('refresh-btn').addEventListener('click', loadAll);

    // 스냅샷(금리/일봉) 폴링 — 유가/환율은 스트림이 실시간 담당
    setInterval(() => {
        if (document.hidden) return;
        refreshMarket();
        if (currentView === 'sector') loadSectors(true);  // 섹터 보기면 섹터도 갱신
    }, MARKET_REFRESH_MS);

    // 계좌 요약·보유 표 자동 갱신: 장중 10초, 장 밖 ≈60초
    let accountTick = 0;
    setInterval(() => {
        if (document.hidden) return;
        accountTick++;
        if (isMarketActive() || accountTick % IDLE_EVERY === 0) refreshAccount();
    }, LIVE_REFRESH_MS);
});
