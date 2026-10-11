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

function renderHoldings(holdings) {
    const body = document.getElementById('holdings-body');
    body.innerHTML = '';
    holdingsList = holdings || [];

    if (!holdings.length) {
        body.innerHTML = '<tr><td colspan="7" class="loading-row">보유 중인 종목이 없습니다.</td></tr>';
        renderHoldingsPie([]);
        return;
    }

    holdings.forEach(h => {
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
            <td class="num ${cls}">${pct(h.pnl_rate)}</td>
        `;
        body.appendChild(tr);
    });

    updatePnlKrw();
    renderHoldingsPie(holdings);
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

async function loadHoldingsCharts() {
    const grid = document.getElementById('holdings-charts-grid');
    if (!grid) return;
    try {
        const d = await fetchJSON(`/api/account/holdings-charts?period=${holdingPeriod}`);
        grid.innerHTML = '';
        (d.charts || []).forEach(ch => {
            const up = (ch.change || 0) >= 0;
            const cur = ch.currency || accountCurrency;
            const cls = up ? 'positive' : 'negative';
            // 보유종목 매입평균가 → 차트 기준선
            const held = (holdingsList || []).find(h => h.code === ch.code);
            const avg = held ? Number(held.avg_price) : NaN;
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
                </div>
                ${chartBlock(ch.series, ch.times, up, curUnit(cur), false, avg)}
            `;
            grid.appendChild(card);
        });
        if (!grid.children.length) {
            grid.innerHTML = '<div class="loading-row">보유 종목이 없습니다.</div>';
        }
    } catch (err) {
        console.error(err);
        grid.innerHTML = '<div class="loading-row">차트를 불러올 수 없습니다.</div>';
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

// 실시간 탭일 때만 주기적으로 전체 차트 갱신
function scheduleHoldingRefresh() {
    if (holdingTimer) { clearInterval(holdingTimer); holdingTimer = null; }
    if (/^[1-5]D$/.test(holdingPeriod)) {
        holdingTimer = setInterval(() => {
            if (!document.hidden) loadHoldingsCharts();
        }, 30000);
    }
}

// ---------------------------------------------------------------------------
// 리밸런싱 (목표 비중 → 조정 수량)
// ---------------------------------------------------------------------------
const RB_KEY = 'assetpro.targetWeights';
let targetWeights = {};
try { targetWeights = JSON.parse(localStorage.getItem(RB_KEY) || '{}') || {}; } catch (_) { targetWeights = {}; }
function saveTargets() { try { localStorage.setItem(RB_KEY, JSON.stringify(targetWeights)); } catch (_) {} }

let rightView = 'charts';

function initRebalance() {
    const vt = document.getElementById('right-view-toggle');
    if (vt) vt.addEventListener('click', (e) => {
        const btn = e.target.closest('button'); if (!btn) return;
        vt.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        rightView = btn.dataset.view;
        const charts = rightView === 'charts';
        document.getElementById('holdings-charts-grid').hidden = !charts;
        document.getElementById('rebalance-panel').hidden = charts;
        document.getElementById('hd-period-toggle').style.display = charts ? '' : 'none';
        document.getElementById('right-title').textContent = charts ? '보유종목 차트' : '목표 비중 & 리밸런싱';
        if (!charts) renderRebalance();
    });

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

// ---------------------------------------------------------------------------
// 시장 지표 (국채금리 / 유가 / 환율)
// ---------------------------------------------------------------------------
let marketData = null;
let currentPeriod = '1D';
const MARKET_REFRESH_MS = 60000;  // 스냅샷(금리/일봉) 폴링 주기

// KIS 실시간이 담당하는 지표(하이브리드). 나머지(금리)는 Yahoo.
const KIS_KEYS = new Set(['wti', 'brent', 'usdkrw']);
const livePrices = {};   // key -> {value, as_of}  (SSE tick 버퍼)
let streamMeta = { mode: null, source: '' };

// 지표 값 포맷 (그룹/단위별)
function fmtValue(ind) {
    const v = ind.value;
    if (ind.unit === '%') return v.toFixed(2) + '<span class="unit">%</span>';
    if (ind.unit === '$') return '<span class="unit">$</span>' + v.toFixed(1);
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
            <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8"
                stroke-linejoin="round" stroke-linecap="round"/>
        </svg>`;
}

// 차트 블록: 스파크라인 + 세로축(최대/최소) 라벨 + 최고/최저 지점 점 + 시간축
// big=true 면 상세차트(큰 높이 + 축 라벨 5개)
function chartBlock(series, times, up, unit, big, refLine) {
    if (!Array.isArray(series) || !series.length) series = [0];
    const sMin = Math.min(...series), sMax = Math.max(...series);  // 시계열 실제 최소/최대
    const hasRef = Number.isFinite(refLine);
    // 기준선(매입평균가)이 범위 밖이면 스케일을 확장해 선이 보이게
    const min = hasRef ? Math.min(sMin, refLine) : sMin;
    const max = hasRef ? Math.max(sMax, refLine) : sMax;
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
    // 매입평균가 기준선 + 라벨
    let refHtml = '';
    if (hasRef) {
        const t = topPct(refLine);
        refHtml = `<span class="spark-refline" style="top:${t.toFixed(1)}%"></span>
            <span class="spark-reflabel" style="top:${t.toFixed(1)}%">평단 ${fmtNum(refLine, unit)}</span>`;
    }
    return `
        <div class="${wrapCls}">
            ${sparkline(series, up, min, max)}
            ${refHtml}
            ${dot(iMax, sMax, 'max')}
            ${dot(iMin, sMin, 'min')}
            <span class="spark-y spark-y-max">${fmtNum(sMax, unit)}</span>
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
            const isKis = KIS_KEYS.has(ind.key);
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

    // 출처: 금리=Yahoo, 유가/환율=KIS 실시간(스트림) — 하이브리드
    const kisSrc = streamMeta.source || 'KIS 연결 대기';
    const mu = document.getElementById('market-updated');
    if (mu) mu.textContent =
        (marketData.updated_at ? '기준: ' + marketData.updated_at.replace('T', ' ') + ' · ' : '')
        + `금리 Yahoo · 유가/환율 ${kisSrc}`;

    // 실시간 표시등: KIS 스트림이 붙어 있으면 초록 펄스
    const dot = document.getElementById('live-dot');
    if (dot) {
        const on = streamMeta.mode === 'live' || streamMeta.mode === 'sim';
        dot.className = 'live-dot ' + (on ? 'on' : 'off');
        dot.title = on ? `KIS 실시간 스트림 수신 중 — ${kisSrc}` : 'KIS 스트림 연결 대기';
    }

    renderMarketCharts();  // 지표 그래프 패널

    // USD→KRW 환율 갱신 후 계좌 원화 병기 업데이트
    const u = marketData.indicators.find(i => i.key === 'usdkrw');
    if (u && u.value) { usdKrwRate = u.value; updateKrwLines(); }
}

// 지표 그래프: 시장지표 5개의 큰 그래프 (시장지표 옆 패널)
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
    loadAll();
    connectStream();  // KIS 실시간 tick (SSE)
    document.getElementById('refresh-btn').addEventListener('click', loadAll);

    // 스냅샷(금리/일봉) 폴링 — 유가/환율은 스트림이 실시간 담당
    setInterval(() => {
        if (!document.hidden) refreshMarket();
    }, MARKET_REFRESH_MS);
});
