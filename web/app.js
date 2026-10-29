'use strict';

// ---------------------------------------------------------------------------
// Constantes de diseño
// ---------------------------------------------------------------------------
const VC = {
  'PASS': { fg: '#5BD69A', bg: 'rgba(91,214,154,0.10)', bd: 'rgba(91,214,154,0.35)' },
  'WARN': { fg: '#F2B84B', bg: 'rgba(242,184,75,0.10)', bd: 'rgba(242,184,75,0.35)' },
  'FAIL': { fg: '#FF7D7D', bg: 'rgba(255,125,125,0.10)', bd: 'rgba(255,125,125,0.40)' },
  'N/A': { fg: '#95A0AE', bg: 'transparent', bd: '#3A4350' },
};
VC.ERR = VC['N/A'];
const IC = {
  'FUERTE': { fg: '#0D1015', bg: '#8DBBFF', bd: '#8DBBFF', bs: 'solid' },
  'MODERADA': { fg: '#B9D4FF', bg: 'rgba(108,168,255,0.22)', bd: 'rgba(108,168,255,0.6)', bs: 'solid' },
  'DEBIL': { fg: '#8DBBFF', bg: 'rgba(108,168,255,0.08)', bd: 'rgba(108,168,255,0.35)', bs: 'solid' },
  '10%-OWNER': { fg: '#C5CCD6', bg: 'transparent', bd: '#4A5563', bs: 'solid' },
  'NINGUNA': { fg: '#95A0AE', bg: 'transparent', bd: '#2A323D', bs: 'solid' },
  'N/A': { fg: '#95A0AE', bg: 'transparent', bd: '#4A5563', bs: 'dashed' },
};
const LABELS = ['1 Rentabilidad', '2 Clientes', '3 Moat', '4 FCF', '5 Valuación'];
const FN = ['Rentabilidad o camino claro', 'Diversificación de clientes', 'Ventaja competitiva durable (moat)', 'Flujo de caja libre', 'Valuación vs. crecimiento'];
const LEGEND = [
  ['FUERTE', 'Grupo que incluye CEO o CFO, o grupo de ≥ 3 insiders en 30 días.'],
  ['MODERADA', 'Grupo sin CEO/CFO, o compra individual de CEO/CFO ≥ USD 100,000.'],
  ['DEBIL', 'Compras aisladas de directores u otros cargos.'],
  ['10%-OWNER', 'Solo compras de accionistas ≥ 10% (empresas, fondos, familias).'],
  ['NINGUNA', 'Form 4 revisados, sin código P.'],
  ['N/A', 'Emisor sin Form 4 (20-F / 40-F) o sin CIK.'],
];
const NAV = [['resumen', 'Resumen'], ['detalle', 'Detalle por ticker'], ['convergencia', 'Convergencia 13F'], ['corrida', 'Nueva corrida']];
const GROUPS = [
  { label: 'Filtro 1 · Rentabilidad', file: 'screener.py · T', items: [['f1_mop', 'Margen operativo mínimo', '%'], ['f1_mb', 'Margen bruto “camino claro”', '%']] },
  { label: 'Filtro 2 · Clientes', file: 'screener.py · T', items: [['f2_w', 'Un cliente: WARN desde', '%'], ['f2_f', 'Un cliente: FAIL desde', '%']] },
  { label: 'Filtro 3 · Moat', file: 'screener.py · T', items: [['f3_mop', 'Margen op. medio mínimo', '%'], ['f3_cv', 'Coef. de variación máx.', ''], ['f3_mb', 'Margen bruto mínimo', '%'], ['f3_roe', 'ROE para moat de costo', '%']] },
  { label: 'Filtro 4 · FCF', file: 'screener.py · T', items: [['f4_y', 'Años mínimos de FCF', 'años'], ['f4_m', 'FCF / ingresos mínimo', '%']] },
  { label: 'Filtro 5 · Valuación', file: 'screener.py · T', items: [['f5_p', 'PEG para PASS ≤', ''], ['f5_w', 'PEG para WARN ≤', ''], ['f5_ev', 'EV/EBITDA sin utilidades ≤', 'x'], ['f5_bad', 'EV/EBITDA corrupto >', 'x'], ['f5_cap', 'Cap de crecimiento', '%']] },
  { label: 'Insiders', file: 'insiders.py', items: [['MONTHS_BACK', 'Ventana hacia atrás', 'meses'], ['CLUSTER_WINDOW_DAYS', 'Ventana de grupo', 'días'], ['CLEVEL_SINGLE_MIN_USD', 'Compra individual CEO/CFO ≥', 'USD']] },
  { label: '13F y cuellos de botella', file: 'institutions.py · bottlenecks.py', items: [['STALE_DAYS', '13F desactualizado si >', 'días'], ['MAX_SENTENCES', 'Frases máx. por fuente', '']] },
];
const MODS = [
  ['ins', 'Insiders · Form 4', 'Compras en mercado abierto (código P), últimos 6 meses.', '--no-insiders'],
  ['f13', '13F · 39 gestores', 'Dos trimestres por gestor + convergencia sectorial. ~4 min la primera vez.', '--no-13f'],
  ['bn', 'Cuellos de botella', 'Texto libre del 8-K Ex. 99.1 y del último 10-Q / 10-K.', '--no-bottlenecks'],
];
const PIPE = [
  [null, 'screen()', '5 filtros por ticker → CompanyResult'],
  ['ins', 'insiders.analyze()', 'Form 4 y 4/A por ticker'],
  ['f13', 'institutions.load_managers()', 'una vez; luego analyze() por ticker y convergence() sobre el universo'],
  ['bn', 'bottlenecks.analyze()', '8-K Ex. 99.1 + 10-Q / 10-K por ticker'],
  [null, 'to_frames()', 'tablas resumen y detalle → CSV, Markdown y consola'],
];
const DEFAULT_TEXT = '# universo de prueba\nMSFT\nNVDA\nAVGO\nSMCI\nASML\nAAPL\nRIVN\nBRK-B\nCOST';
const TICKER_RE = /^[A-Z][A-Z0-9]{0,5}([.\-][A-Z0-9]{1,3})?$/;

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
const S = {
  view: 'resumen', data: null, run: { running: false }, defaults: {}, runErr: '',
  sel: null, filter: 'all', sort: 'pasa',
  det: null, grp: 'todos',
  cv: { sector: null, mode: 'pos', grp: 'todos', sel: null },
  cfg: { text: DEFAULT_TEXT, mods: { ins: true, f13: true, bn: true }, th: {}, out: 'resultados', ua: '' },
  copied: false, timer: null,
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pasaOf = (c) => c.v.filter((x) => x === 'PASS').length;
const okCos = () => (S.data ? S.data.companies.filter((c) => !c.error) : []);
const minus = (s) => String(s).replace('-', '−');

function loadDraft() {
  try {
    const d = JSON.parse(localStorage.getItem('screener-draft') || 'null');
    if (d) {
      if (typeof d.text === 'string') S.cfg.text = d.text;
      if (d.mods) S.cfg.mods = Object.assign(S.cfg.mods, d.mods);
      if (d.th) S.cfg.th = d.th;
      if (typeof d.out === 'string') S.cfg.out = d.out;
    }
  } catch (e) { /* sin almacenamiento: se ignora */ }
}
function saveDraft() {
  try {
    const { text, mods, th, out } = S.cfg;
    localStorage.setItem('screener-draft', JSON.stringify({ text, mods, th, out }));
  } catch (e) { /* ignorar */ }
}

// ---------------------------------------------------------------------------
// Piezas reutilizables
// ---------------------------------------------------------------------------
function seg(label, items, current, act) {
  return `<div class="seg" role="group" aria-label="${esc(label)}">${items.map(([v, l]) =>
    `<button type="button" aria-pressed="${current === v}" data-act="${act}" data-v="${esc(v)}">${esc(l)}</button>`).join('')}</div>`;
}
function chip(v, extra = '') {
  const c = VC[v] || VC['N/A'];
  return `<span class="chip" style="color:${c.fg};background:${c.bg};border-color:${c.bd};${extra}">${esc(v)}</span>`;
}
function insChip(label, size = 'sm') {
  const c = IC[label] || IC['N/A'];
  const pad = size === 'lg' ? '4px 10px' : '2px 7px';
  const fs = size === 'lg' ? 13 : 11;
  return `<span class="mono" style="align-self:flex-start;padding:${pad};border-radius:4px;font-size:${fs}px;font-weight:600;letter-spacing:.04em;color:${c.fg};background:${c.bg};border:1px ${c.bs} ${c.bd}">${esc(label)}</span>`;
}
function emptyState(msg) {
  return `<main><div class="card" style="padding:32px;display:flex;flex-direction:column;gap:12px;align-items:flex-start">
    <h1>Sin resultados todavía</h1>
    <p style="margin:0;color:#B4BDC9;max-width:640px">${esc(msg)}</p>
    <button type="button" class="btn" data-act="nav" data-v="corrida" style="background:#1F3A5F;border-color:#1F3A5F">Configurar una corrida</button></div></main>`;
}
function th(key) {
  const m = S.data?.meta?.th || {};
  return m[key] ?? S.defaults[key] ?? '';
}

// ---------------------------------------------------------------------------
// Cabecera
// ---------------------------------------------------------------------------
function header() {
  return `<header class="top">
    <div style="display:flex;align-items:baseline;gap:12px">
      <span class="mono" style="font-weight:600;font-size:15px;letter-spacing:.08em">SCREENER</span>
      <span class="mono muted" style="font-size:12px">fundamental · insiders · 13F</span>
    </div>
    <nav class="nav" aria-label="Secciones">${NAV.map(([k, l]) =>
      `<button type="button" data-act="nav" data-v="${k}" ${S.view === k ? 'aria-current="page"' : ''}>${l}</button>`).join('')}</nav>
  </header>`;
}

// ---------------------------------------------------------------------------
// Resumen
// ---------------------------------------------------------------------------
function viewResumen() {
  if (!S.data) return emptyState('Aún no has corrido el screener. Define el universo de tickers y los módulos en “Nueva corrida”.');
  const cos = okCos();
  const meta = S.data.meta;
  const all = S.data.companies;
  if (!S.sel || !all.find((c) => c.t === S.sel)) S.sel = (cos[0] || all[0]).t;

  const insOn = meta.modules.ins, f13On = meta.modules.f13;
  const buys = cos.filter((c) => c.ins && c.ins.label !== 'NINGUNA' && c.ins.label !== 'N/A');
  const strong = buys.filter((c) => c.ins.label === 'FUERTE' || c.ins.label === 'MODERADA');
  const five = cos.filter((c) => pasaOf(c) === 5), ge4 = cos.filter((c) => pasaOf(c) >= 4);
  const anyFail = cos.filter((c) => c.v.includes('FAIL'));
  const names = (l) => l.map((c) => c.t).join(' ');
  const kpis = [
    ['Pasan 5 de 5', five.length, names(five)],
    ['Pasan 4 o más', ge4.length, names(ge4)],
    ['Con algún FAIL', anyFail.length, names(anyFail)],
    ['Con compras de insiders (P)', insOn ? buys.length : '—', insOn ? (strong.length ? names(strong) : 'ninguna FUERTE ni MODERADA') : 'módulo desactivado'],
  ];

  let list = all.slice();
  if (S.filter === 'ge4') list = list.filter((c) => !c.error && pasaOf(c) >= 4);
  if (S.filter === 'fail') list = list.filter((c) => !c.error && c.v.includes('FAIL'));
  if (S.filter === 'ins') list = list.filter((c) => c.ins && c.ins.label !== 'NINGUNA' && c.ins.label !== 'N/A');
  list.sort((a, b) => {
    if (a.error !== b.error) return a.error ? 1 : -1;
    if (a.error) return a.t < b.t ? -1 : 1;
    if (S.sort === 'ticker') return a.t < b.t ? -1 : 1;
    if (S.sort === 'f13') return ((b.f13 && b.f13.nums[0]) || 0) - ((a.f13 && a.f13.nums[0]) || 0);
    return pasaOf(b) - pasaOf(a) || (a.t < b.t ? -1 : 1);
  });

  const cols = LABELS.map((label, i) => {
    let p = 0, w = 0, f = 0;
    cos.forEach((c) => { if (c.v[i] === 'PASS') p++; else if (c.v[i] === 'WARN') w++; else if (c.v[i] === 'FAIL') f++; });
    const n = cos.length || 1;
    return `<span style="display:flex;flex-direction:column;gap:6px"><span style="color:#C5CCD6">${label}</span>
      <span aria-hidden="true" style="display:flex;height:4px;border-radius:2px;overflow:hidden;background:#222A34">
        <span style="width:${p / n * 100}%;background:#5BD69A"></span><span style="width:${w / n * 100}%;background:#F2B84B"></span><span style="width:${f / n * 100}%;background:#FF7D7D"></span></span>
      <span class="mono" style="font-size:11px">${p}P ${w}W ${f}F</span></span>`;
  }).join('');

  const GRID = '190px repeat(5,84px) 84px minmax(170px,1fr) 150px';
  const rows = list.map((c) => {
    const on = c.t === S.sel;
    const base = `grid-template-columns:${GRID};gap:0 10px;align-items:center;padding:12px 16px;border-bottom:1px solid #1D242D;box-shadow:inset 3px 0 0 ${on ? '#8DBBFF' : 'transparent'};${on ? 'background:#1A2230;' : ''}`;
    if (c.error) {
      return `<button type="button" class="rowbtn" aria-pressed="${on}" data-act="sel" data-v="${esc(c.t)}" style="${base}">
        <span class="mono" style="font-size:15px;font-weight:600">${esc(c.t)}</span>
        <span style="grid-column:2/-1;font-size:12px;color:#FF9C9C">${esc(c.n)}</span></button>`;
    }
    const ins = c.ins
      ? `<span style="display:flex;flex-direction:column;gap:4px;min-width:0">${insChip(c.ins.label)}<span style="font-size:12px;color:#95A0AE">${esc(c.ins.row)}</span></span>`
      : `<span class="muted" style="font-size:12px">—</span>`;
    let f13 = `<span class="muted" style="font-size:12px">—</span>`;
    if (c.f13) {
      const [h, up, dn, out] = c.f13.nums;
      f13 = c.f13.na
        ? `<span class="muted" style="font-size:12px">N/A</span>`
        : `<span style="display:flex;flex-direction:column;gap:5px">
            <span class="mono" style="font-size:14px;font-weight:600">${h}<span class="muted" style="font-weight:400;font-size:12px">/${c.f13.n_active}</span></span>
            <span aria-hidden="true" style="display:flex;align-items:center;height:6px">
              <span style="display:flex;justify-content:flex-end;width:72px"><span style="width:${Math.min(72, dn * 5)}px;height:6px;background:#FF7D7D;border-radius:1px 0 0 1px"></span></span>
              <span style="width:1px;height:10px;background:#4A5563"></span>
              <span style="width:${Math.min(72, up * 5)}px;height:6px;background:#5BD69A;border-radius:0 1px 1px 0"></span></span>
            <span class="mono muted" style="font-size:11px"><span style="color:#5BD69A">+${up}</span> <span style="color:#FF7D7D">−${dn}</span> · salió ${out}</span></span>`;
    }
    return `<button type="button" class="rowbtn" aria-pressed="${on}" data-act="sel" data-v="${esc(c.t)}" style="${base}">
      <span style="display:flex;flex-direction:column;gap:2px;min-width:0"><span class="mono" style="font-size:15px;font-weight:600">${esc(c.t)}</span>
        <span class="muted" style="font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.n)}</span></span>
      ${c.v.map((v) => chip(v)).join('')}
      <span style="display:flex;flex-direction:column;gap:5px"><span class="mono" style="font-size:15px;font-weight:600">${pasaOf(c)}<span class="muted" style="font-weight:400;font-size:12px">/5</span></span>
        <span aria-hidden="true" style="display:flex;gap:2px">${c.v.map((v) => `<span style="width:10px;height:4px;border-radius:1px;background:${(VC[v] || VC['N/A']).fg}"></span>`).join('')}</span></span>
      ${ins}${f13}</button>`;
  }).join('');

  const sd = all.find((c) => c.t === S.sel);
  let side = '';
  if (sd && !sd.error) {
    const insFull = sd.ins ? `${sd.ins.label}: ${sd.ins.note}.` : 'Módulo desactivado en esta corrida.';
    let f13txt = 'Módulo desactivado en esta corrida.';
    if (sd.f13) f13txt = sd.f13.na ? `N/A: ${sd.f13.note}` : `${sd.f13.nums[0]}/${sd.f13.n_active} activos/hedge (+${sd.f13.nums[1]} −${sd.f13.nums[2]} salió ${sd.f13.nums[3]})`;
    side = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <div style="display:flex;flex-direction:column;gap:2px"><span class="mono" style="font-size:22px;font-weight:600">${esc(sd.t)}</span>
          <span style="font-size:13px;color:#C5CCD6">${esc(sd.n)}</span><span class="muted" style="font-size:12px">${esc(sd.s)}</span></div>
        <span class="mono muted" style="font-size:13px;white-space:nowrap">pasa <span style="color:#E7EAEE;font-size:18px;font-weight:600">${pasaOf(sd)}</span>/5</span></div>
      <div style="display:flex;flex-direction:column;gap:14px">${FN.map((_, i) => {
        const c = VC[sd.v[i]] || VC['N/A'];
        return `<div style="display:flex;flex-direction:column;gap:6px;padding-bottom:14px;border-bottom:1px solid #1D242D">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px"><span style="font-size:13px;font-weight:600">${LABELS[i]}</span>
            <span class="mono" style="padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;letter-spacing:.04em;color:${c.fg};background:${c.bg};border:1px solid ${c.bd}">${esc(sd.v[i])}</span></div>
          <p style="margin:0;font-size:12.5px;color:#B4BDC9">${esc(sd.notes[i])}</p></div>`;
      }).join('')}</div>
      <div style="display:flex;flex-direction:column;gap:6px"><span class="muted" style="font-size:12px">Insiders (Form 4, ${esc(th('MONTHS_BACK'))} meses)</span><span style="font-size:13px;color:#C5CCD6">${esc(insFull)}</span></div>
      <div style="display:flex;flex-direction:column;gap:6px"><span class="muted" style="font-size:12px">13F institucionales</span><span class="mono" style="font-size:13px;color:#C5CCD6">${esc(f13txt)}</span></div>
      <button type="button" data-act="open" data-v="${esc(sd.t)}" style="display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:0 16px;border-radius:6px;border:0;background:#1F3A5F;color:#E7EAEE;font-size:13px;font-weight:500;cursor:pointer">Abrir detalle completo
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg></button>`;
  }

  const sub = [`salida: ${meta.out}`, `${meta.n} tickers`];
  if (meta.period) sub.push(`13F más reciente ${meta.period}`);
  if (meta.stale.length) sub.push(`excluido de la señal: ${meta.stale.join(', ')} (13F desactualizado)`);

  return `<main>
    <div style="display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px">
      <div style="display:flex;flex-direction:column;gap:6px"><h1>Resumen de corrida</h1>
        <p class="mono muted" style="margin:0;font-size:12px">${esc(sub.join(' · '))}</p></div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        <a class="btn" href="/api/download/resumen" download>${esc(meta.out)}_resumen.csv</a>
        <a class="btn" href="/api/download/detalle" download>${esc(meta.out)}_detalle.csv</a>
        <a class="btn" href="/api/download/md" download>${esc(meta.out)}.md</a></div></div>
    <div class="card" style="display:flex;gap:12px;align-items:flex-start;padding:12px 16px;background:#12171E;border-color:#2A323D">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#95A0AE" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none;margin-top:2px"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4"></path><path d="M12 8h.01"></path></svg>
      <p style="margin:0;font-size:13px;color:#C5CCD6">No es una recomendación de compra. Cinco PASS significa que la empresa pasó cinco reglas mecánicas con datos gratuitos y a veces sucios; la decisión de entrar o no es tuya. Proyecto educativo: no es asesoría financiera.</p></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">${kpis.map(([l, v, s]) =>
      `<div class="card" style="display:flex;flex-direction:column;gap:4px;padding:14px 16px"><span class="muted" style="font-size:12px">${esc(l)}</span>
        <span class="mono" style="font-size:26px;font-weight:500">${esc(v)}</span><span class="mono muted" style="font-size:12px">${esc(s)}</span></div>`).join('')}</div>
    <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px">
      ${seg('Filtrar', [['all', 'Todos'], ['ge4', 'Pasa ≥ 4'], ['fail', 'Con algún FAIL'], ['ins', 'Con compras de insiders']], S.filter, 'filter')}
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px"><span class="muted" style="font-size:12px">Ordenar por</span>
        ${seg('Ordenar', [['pasa', 'Pasa'], ['ticker', 'Ticker'], ['f13', 'Gestores 13F']], S.sort, 'sort')}</div></div>
    <div style="display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start">
      <section aria-label="Tabla de resultados" class="card" style="flex:999 1 760px;min-width:0;overflow:hidden"><div style="overflow-x:auto"><div style="min-width:1000px">
        <div style="display:grid;grid-template-columns:${GRID};gap:0 10px;align-items:end;padding:12px 16px;border-bottom:1px solid #222A34;background:#10141A;font-size:12px;color:#95A0AE">
          <span>Ticker</span>${cols}<span style="color:#C5CCD6">Pasa</span><span style="color:#C5CCD6">Insiders 6m</span><span style="color:#C5CCD6">13F activos/hedge</span></div>
        ${rows || '<p style="margin:0;padding:32px 16px;text-align:center;color:#95A0AE">Ningún ticker cumple este filtro.</p>'}
      </div></div></section>
      <aside aria-label="Notas del ticker seleccionado" class="card" style="flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:16px;padding:20px">${side}</aside>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px;padding-top:4px">
      ${f13On ? '<p class="muted" style="margin:0;font-size:12px"><span style="color:#C5CCD6;font-weight:600">Aviso 13F:</span> se presentan hasta 45 días después del cierre de trimestre. La foto tiene entre 1.5 y 4.5 meses de antigüedad y solo muestra posiciones largas en acciones de EE.UU. (sin cortos, sin coberturas). Los gestores pasivos no cuentan en esta columna.</p>' : ''}
      ${insOn ? '<p class="muted" style="margin:0;font-size:12px"><span style="color:#C5CCD6;font-weight:600">Insiders:</span> solo compras en mercado abierto con dinero propio (Form 4 código P). FUERTE = CEO/CFO o ≥3 insiders en 30 días; MODERADA = varios insiders o compra grande de CEO/CFO; DEBIL = compra aislada; 10%-OWNER = solo accionistas mayoritarios.</p>' : ''}
    </div></main>`;
}

// ---------------------------------------------------------------------------
// Detalle por ticker
// ---------------------------------------------------------------------------
function rules() {
  return [
    `PASS si margen operativo ≥ ${th('f1_mop')}% y la utilidad operativa no cae todos los años. WARN si es positivo < ${th('f1_mop')}%, o pérdidas que se achican con margen bruto ≥ ${th('f1_mb')}%.`,
    `Regex sobre el último 10-K / 20-F / 40-F. Sin mención → PASS. Un cliente ${th('f2_w')}–${th('f2_f')}% → WARN; ≥ ${th('f2_f')}% → FAIL.`,
    `Margen op. medio ≥ ${th('f3_mop')}%, coef. de variación ≤ ${th('f3_cv')}, margen bruto ≥ ${th('f3_mb')}%, más señales cualitativas en la descripción. PASS solo si ambos.`,
    `PASS si FCF > 0 en todos los años (mín. ${th('f4_y')}) y FCF/ingresos ≥ ${th('f4_m')}%.`,
    `PEG = P/E forward ÷ crecimiento (mediana de 3 estimaciones, capada a ${th('f5_cap')}%). ≤ ${th('f5_p')} PASS, ≤ ${th('f5_w')} WARN.`,
  ];
}

function highlight(text) {
  const kws = S.data?.meta?.keywords || [];
  let re = null;
  try { re = kws.length ? new RegExp('(' + kws.join('|') + ')', 'gi') : null; } catch (e) { re = null; }
  if (!re) return esc(text);
  let out = '', last = 0, m;
  while ((m = re.exec(text))) {
    if (m[0] === '') { re.lastIndex++; continue; }
    out += esc(text.slice(last, m.index)) + `<span style="color:#F2D59A;background:rgba(242,184,75,0.14);border-radius:2px">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

function viewDetalle() {
  if (!S.data) return emptyState('Corre el screener para ver el detalle de cada ticker.');
  const cos = okCos();
  if (!cos.length) return emptyState('Ningún ticker se pudo analizar en la última corrida. Revisa el Resumen para ver los errores.');
  if (!S.det || !cos.find((c) => c.t === S.det)) S.det = cos[0].t;
  const d = cos.find((c) => c.t === S.det);
  const meta = S.data.meta;
  const R = rules();

  const tabs = cos.map((c) => {
    const on = c.t === S.det;
    return `<button type="button" aria-pressed="${on}" data-act="det" data-v="${esc(c.t)}" style="display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 12px;border-radius:6px;border:1px solid ${on ? '#8DBBFF' : '#222A34'};background:${on ? '#1A2230' : '#141920'};color:#E7EAEE;cursor:pointer">
      <span class="mono" style="font-size:13px;font-weight:600">${esc(c.t)}</span>
      <span aria-hidden="true" style="display:flex;gap:2px">${c.v.map((v) => `<span style="width:6px;height:14px;border-radius:1px;background:${(VC[v] || VC['N/A']).fg}"></span>`).join('')}</span>
      <span class="mono muted" style="font-size:11px">${pasaOf(c)}/5</span></button>`;
  }).join('');

  const filters = d.v.map((v, k) => {
    const c = VC[v] || VC['N/A'];
    return `<article style="display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:4px 14px;padding:16px 18px;border:1px solid #222A34;border-radius:8px;background:#141920;box-shadow:inset 3px 0 0 ${c.fg}">
      <span class="mono" style="font-size:20px;font-weight:600;color:#4A5563">${k + 1}</span>
      <div style="display:flex;flex-direction:column;gap:6px;min-width:0"><h3 style="margin:0;font-size:15px;font-weight:600">${FN[k]}</h3>
        <p style="margin:0;font-size:13.5px;color:#C5CCD6">${esc(d.notes[k])}</p>
        ${d.q[k] ? `<blockquote class="mono" style="margin:4px 0 0;padding:8px 12px;border-left:2px solid #3A4350;background:#10141A;font-size:12px;color:#B4BDC9">«${esc(d.q[k])}»</blockquote>` : ''}
        <p class="muted" style="margin:2px 0 0;font-size:12px">Regla: ${esc(R[k])}</p></div>
      ${chip(v, 'align-self:start')}</article>`;
  }).join('');

  let insBlock;
  if (!d.ins) {
    insBlock = `<p class="muted" style="margin:0;font-size:13.5px">Módulo de insiders desactivado en esta corrida.</p>`;
  } else {
    insBlock = `<div style="display:flex;flex-direction:column;gap:8px">${insChip(d.ins.label, 'lg')}<p style="margin:0;font-size:13.5px;color:#C5CCD6">${esc(d.ins.note)}.</p></div>
      ${d.ins.buys.length ? `<div style="display:flex;flex-direction:column;gap:8px"><span class="muted" style="font-size:12px">Compras con código P · total ${esc(d.ins.total)}</span>
        ${d.ins.buys.map((b) => `<div style="display:flex;flex-direction:column;gap:4px;padding:10px 12px;border:1px solid #1D242D;border-radius:6px;background:#10141A">
          <div style="display:flex;justify-content:space-between;gap:8px"><span style="font-size:13px;font-weight:600">${esc(b.who)}</span><span class="mono muted" style="font-size:12px">${esc(b.d)}</span></div>
          <span class="mono" style="font-size:12px;color:#C5CCD6">${esc(b.sh)} × ${esc(b.px)} = <span style="color:#E7EAEE;font-weight:600">${esc(b.usd)}</span></span>
          <div style="display:flex;flex-wrap:wrap;gap:6px"><span style="padding:1px 7px;border-radius:4px;border:1px solid #2A323D;font-size:11px;color:#B4BDC9">${esc(b.role)}</span>
            ${b.tags.map((t) => `<span style="padding:1px 7px;border-radius:4px;border:1px dashed #F2B84B;font-size:11px;color:#F2B84B">${esc(t)}</span>`).join('')}</div></div>`).join('')}</div>` : ''}
      <div style="display:flex;flex-direction:column;gap:6px;padding-top:12px;border-top:1px solid #1D242D"><span class="muted" style="font-size:12px">Escala de la señal</span>
        ${LEGEND.map(([k, t]) => { const on = k === d.ins.label; return `<div style="display:grid;grid-template-columns:92px minmax(0,1fr);gap:8px;align-items:baseline;padding:4px 6px;border-radius:4px;background:${on ? '#1F2A3A' : 'transparent'}">
          <span class="mono" style="font-size:11px;font-weight:600;color:${on ? '#E7EAEE' : '#95A0AE'}">${k}</span><span style="font-size:12px;color:${on ? '#E7EAEE' : '#95A0AE'}">${t}</span></div>`; }).join('')}</div>`;
  }

  // 13F
  let f13Block;
  if (!d.f13) {
    f13Block = `<section style="display:flex;flex-direction:column;gap:10px"><h2 class="h2s">13F institucionales</h2><p class="muted" style="margin:0;font-size:13.5px">Módulo 13F desactivado en esta corrida.</p></section>`;
  } else if (d.f13.na) {
    f13Block = `<section style="display:flex;flex-direction:column;gap:10px"><h2 class="h2s">13F institucionales</h2><p class="muted" style="margin:0;font-size:13.5px">N/A: ${esc(d.f13.note)}</p></section>`;
  } else {
    const order = { hedge: 0, activo: 1 };
    const rowsAll = d.f13.rows.slice().sort((a, b) => (order[a.grp] ?? 2) - (order[b.grp] ?? 2) || b.pct - a.pct);
    const rows = rowsAll.filter((r) => S.grp === 'todos' || r.grp === S.grp);
    const [h, up, dn, out] = d.f13.nums;
    const COLS = 'minmax(200px,1.4fr) 80px 104px 130px 100px 180px 90px';
    const P = meta.period;
    const body = rows.map((r) => {
      const isUp = r.ch === 'NUEVA' || r.ch.startsWith('+'), isDn = r.ch === 'SALIO' || r.ch.startsWith('-');
      const chC = isUp ? '#5BD69A' : (isDn ? '#FF7D7D' : '#95A0AE');
      return `<div style="display:grid;grid-template-columns:${COLS};gap:0 12px;align-items:center;padding:9px 16px;border-bottom:1px solid #1D242D;font-size:13px">
        <span style="font-weight:500">${esc(r.g)}</span><span class="tag">${esc(r.grp)}</span>
        <span class="mono" style="font-size:12px;color:${r.per === P ? '#95A0AE' : '#F2B84B'}">${esc(r.per)}</span>
        <span class="mono" style="font-size:12px;text-align:right">${esc(r.sh)}</span><span class="mono" style="font-size:12px;text-align:right">${esc(r.val)}</span>
        <span style="display:flex;align-items:center;gap:8px"><span aria-hidden="true" style="flex:1;height:6px;border-radius:1px;background:#1D242D"><span style="display:block;width:${Math.min(100, r.pct * 4.5)}%;height:6px;border-radius:1px;background:#6CA8FF"></span></span>
          <span class="mono" style="font-size:12px;width:52px;text-align:right">${r.pct.toFixed(2)}%</span></span>
        <span class="mono" style="font-size:12px;font-weight:600;text-align:right;color:${chC}">${esc(r.ch === 'SALIO' ? 'SALIÓ' : minus(r.ch))}</span></div>`;
    }).join('');
    f13Block = `<section style="display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px">
        <div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:12px"><h2 class="h2s">13F institucionales</h2>
          <span class="mono" style="font-size:13px;color:#C5CCD6">${h}/${d.f13.n_active} activos/hedge (+${up} −${dn} salió ${out})</span></div>
        ${seg('Grupo de gestor', [['todos', 'Todos'], ['hedge', 'Hedge'], ['activo', 'Activos']], S.grp, 'grp')}</div>
      <div class="card" style="overflow:hidden"><div style="overflow-x:auto"><div style="min-width:900px">
        <div style="display:grid;grid-template-columns:${COLS};gap:0 12px;padding:10px 16px;border-bottom:1px solid #222A34;background:#10141A;font-size:12px;color:#95A0AE">
          <span>Gestor</span><span>Grupo</span><span>Periodo</span><span style="text-align:right">Acciones</span><span style="text-align:right">Valor USD</span><span>% de su 13F</span><span style="text-align:right">vs. trim. ant.</span></div>
        ${body || '<p class="muted" style="margin:0;padding:24px 16px;text-align:center">Ningún gestor de este grupo reporta posición.</p>'}
      </div></div>
      <p class="muted" style="margin:0;padding:10px 16px;font-size:12px">Todos los gestores activo/hedge con fila en el 13F (${rowsAll.length}). Los pasivos (Vanguard, BlackRock, State Street…) se ocultan: reflejan flujos de índices, no convicción. % de su 13F = proxy de convicción.</p></div></section>`;
  }

  let bnBlock;
  if (!d.bn) {
    bnBlock = `<p class="muted" style="margin:0;font-size:13.5px">Módulo de cuellos de botella desactivado en esta corrida.</p>`;
  } else {
    bnBlock = `<span class="mono muted" style="font-size:12px">Fuentes: ${esc(d.bn.src || 'ninguna')}</span>
      ${d.bn.items.length
        ? `<ol style="margin:0;padding:0 0 0 22px;display:flex;flex-direction:column;gap:10px;color:#4A5563;font-family:'IBM Plex Mono',monospace;font-size:12px">${d.bn.items.map((s) =>
          `<li style="padding-left:6px"><span style="font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:13.5px;color:#C5CCD6">${highlight(s)}</span></li>`).join('')}</ol>`
        : `<p class="muted" style="margin:0;font-size:13.5px">${esc(d.bn.note || 'Sin menciones de restricciones de oferta o capacidad en los documentos revisados.')}</p>`}
      <p class="muted" style="margin:0;padding-top:10px;border-top:1px solid #1D242D;font-size:12px">Resaltado: vocabulario de restricción que dispara la extracción. Se descartan hipotéticos (may, could, might) y boilerplate. Máximo ${esc(th('MAX_SENTENCES'))} frases por fuente.</p>`;
  }

  return `<main style="padding-top:24px">
    <div role="group" aria-label="Elegir ticker" style="display:flex;flex-wrap:wrap;gap:6px">${tabs}</div>
    <section aria-label="Empresa" style="display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;padding:4px 0 8px;border-bottom:1px solid #222A34">
      <div style="display:flex;flex-direction:column;gap:6px"><div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:14px">
        <h1 class="mono" style="font-size:40px">${esc(d.t)}</h1><span style="font-size:18px;color:#C5CCD6">${esc(d.n)}</span></div>
        ${d.s ? `<span style="align-self:flex-start;padding:3px 10px;border-radius:999px;border:1px solid #2A323D;font-size:12px;color:#B4BDC9">${esc(d.s)}</span>` : ''}</div>
      <div style="display:flex;align-items:center;gap:16px"><div style="display:flex;gap:4px" aria-hidden="true">${d.v.map((v) => `<span style="width:28px;height:8px;border-radius:2px;background:${(VC[v] || VC['N/A']).fg}"></span>`).join('')}</div>
        <span class="mono muted" style="font-size:14px">pasa <span style="font-size:28px;font-weight:600;color:#E7EAEE">${pasaOf(d)}</span>/5</span></div></section>
    <div style="display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start">
      <section style="flex:999 1 620px;min-width:0;display:flex;flex-direction:column;gap:10px"><h2 class="h2s">Cinco filtros</h2>${filters}</section>
      <section style="flex:1 1 380px;min-width:0;display:flex;flex-direction:column;gap:10px"><h2 class="h2s">Insiders · Form 4 · ${esc(th('MONTHS_BACK'))} meses</h2>
        <div class="card" style="display:flex;flex-direction:column;gap:16px;padding:18px">${insBlock}</div></section></div>
    ${f13Block}
    <section style="display:flex;flex-direction:column;gap:10px"><div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:12px"><h2 class="h2s">Cuellos de botella que reporta la empresa</h2><span class="muted" style="font-size:12px">texto libre, no es filtro</span></div>
      <div class="card" style="display:flex;flex-direction:column;gap:12px;padding:18px">${bnBlock}</div></section>
    ${meta.modules.f13 ? `<p class="muted" style="margin:0;font-size:12px"><span style="color:#C5CCD6;font-weight:600">Aviso 13F:</span> periodo más reciente ${esc(meta.period || 'n/d')}. Rezago de hasta 45 días tras el cierre de trimestre: la foto tiene entre 1.5 y 4.5 meses. Solo posiciones largas en acciones de EE.UU.; una posición larga puede ser una pata de una cobertura que no se ve.</p>` : ''}
  </main>`;
}

// ---------------------------------------------------------------------------
// Convergencia 13F
// ---------------------------------------------------------------------------
function viewConvergencia() {
  if (!S.data) return emptyState('Corre el screener con el módulo 13F activo para ver la convergencia sectorial.');
  const cv = S.data.convergence, meta = S.data.meta;
  if (!cv) return emptyState('La última corrida se hizo sin el módulo 13F. Actívalo en “Nueva corrida”.');
  if (!cv.sectors.length) {
    const single = cv.single.map((s) => `${s.sector} (${s.tickers.join(', ')})`).join('; ');
    return emptyState('Se necesitan al menos 2 empresas del mismo sector en el universo para comparar gestores.' + (single ? ' Sectores con una sola: ' + single + '.' : ''));
  }
  if (!S.cv.sector || !cv.sectors.find((s) => s.sector === S.cv.sector)) S.cv.sector = cv.sectors[0].sector;
  const sec = cv.sectors.find((s) => s.sector === S.cv.sector);
  const T = sec.tickers, N = T.length;

  const list = sec.managers.filter((m) => {
    if (S.cv.grp !== 'todos' && m.grp !== S.cv.grp) return false;
    if (S.cv.mode === 'up' && m.up.length < 2) return false;
    return true;
  }).sort((a, b) => {
    if (S.cv.mode === 'up') return b.up.length - a.up.length || b.held.length - a.held.length;
    return b.held.length - a.held.length || (a.g < b.g ? -1 : 1);
  });
  if (!S.cv.sel || !sec.managers.find((m) => m.g === S.cv.sel)) S.cv.sel = (list[0] || sec.managers[0] || {}).g;
  const sm = sec.managers.find((m) => m.g === S.cv.sel);

  const GRID = `minmax(200px,1fr) 72px repeat(${N},76px) 64px`;
  const cell = (m, t) => {
    const h = m.held.includes(t), u = m.up.includes(t);
    let x = '·', bg = 'transparent', bd = 'transparent', fg = '#3A4350';
    if (u) { x = '+'; bg = 'rgba(91,214,154,0.14)'; bd = 'rgba(91,214,154,0.6)'; fg = '#5BD69A'; }
    else if (h) { x = 'POS'; if (S.cv.mode === 'up') { bd = '#2A323D'; fg = '#8E99A7'; } else { bg = '#22324A'; bd = '#34507A'; fg = '#B9D4FF'; } }
    return `<span style="display:flex;justify-content:center"><span class="mono" style="display:inline-flex;align-items:center;justify-content:center;width:56px;height:26px;border-radius:4px;font-size:11px;font-weight:600;background:${bg};border:1px solid ${bd};color:${fg}">${x}</span></span>`;
  };
  const rows = list.map((m) => {
    const on = m.g === S.cv.sel;
    return `<button type="button" class="rowbtn" aria-pressed="${on}" data-act="cvsel" data-v="${esc(m.g)}" style="grid-template-columns:${GRID};gap:0 8px;align-items:center;padding:6px 16px;border-bottom:1px solid #1D242D;box-shadow:inset 3px 0 0 ${on ? '#8DBBFF' : 'transparent'};${on ? 'background:#1A2230;' : ''}">
      <span style="font-size:13px;font-weight:500">${esc(m.g)}</span><span class="tag">${esc(m.grp)}</span>${T.map((t) => cell(m, t)).join('')}
      <span class="mono" style="font-size:13px;font-weight:600;text-align:right">${m.held.length}/${N}</span></button>`;
  }).join('');
  const colHead = T.map((t) => `<span class="mono" style="font-size:12px;font-weight:600;color:#C5CCD6;text-align:center">${esc(t)}</span>`).join('');
  const colFoot = T.map((t) => `<span style="display:flex;flex-direction:column;align-items:center;gap:4px"><span class="mono" style="font-size:13px;font-weight:600;color:#E7EAEE">${sec.managers.filter((m) => m.held.includes(t)).length}</span>
    <span class="mono" style="font-size:11px;color:#5BD69A">+${sec.managers.filter((m) => m.up.includes(t)).length}</span></span>`).join('');

  const nMulti = sec.managers.length, nUp = sec.managers.filter((m) => m.up.length >= 2).length;
  const passive = meta.passive || [];
  const lagTxt = `Periodo más reciente usado: <span class="mono" style="color:#F2D59A">${esc(meta.period || 'n/d')}</span>. Los 13F llegan hasta 45 días después del cierre: esta foto tiene entre 1.5 y 4.5 meses y solo muestra largos en acciones de EE.UU.${meta.stale.length ? ' Excluido por 13F desactualizado: ' + esc(meta.stale.join(', ')) + '.' : ''}`;

  const sectorSeg = cv.sectors.length > 1 ? seg('Sector', cv.sectors.map((s) => [s.sector, s.sector]), S.cv.sector, 'cvsector') : '';

  return `<main>
    <div style="display:flex;flex-direction:column;gap:6px"><h1>Convergencia sectorial 13F</h1>
      <p style="margin:0;max-width:820px;font-size:13.5px;color:#B4BDC9">Gestores activos y hedge con posición en dos o más empresas del mismo sector <span style="color:#E7EAEE;font-weight:600">dentro del universo analizado</span>. No es una lectura de todo el mercado.</p></div>
    <div style="display:flex;gap:12px;align-items:flex-start;padding:12px 16px;border:1px solid rgba(242,184,75,0.35);border-radius:8px;background:rgba(242,184,75,0.06)">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#F2B84B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none;margin-top:2px"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg>
      <p style="margin:0;font-size:13px;color:#E7DCC4">${lagTxt}</p></div>
    ${sectorSeg ? `<div>${sectorSeg}</div>` : ''}
    <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px">
      <div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:12px"><h2 style="margin:0;font-size:18px;font-weight:600">${esc(sec.sector)}</h2><span class="mono muted" style="font-size:12px">${esc(T.join(' '))}</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">${seg('Qué mostrar', [['pos', 'Posiciones'], ['up', 'Solo los que aumentaron']], S.cv.mode, 'cvmode')}${seg('Grupo', [['todos', 'Todos'], ['hedge', 'Hedge'], ['activo', 'Activos']], S.cv.grp, 'cvgrp')}</div></div>
    <div style="display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start">
      <section aria-label="Matriz gestor por empresa" class="card" style="flex:999 1 720px;min-width:0;overflow:hidden"><div style="overflow-x:auto"><div style="min-width:${300 + N * 84}px">
        <div style="display:grid;grid-template-columns:${GRID};gap:0 8px;align-items:end;padding:12px 16px;border-bottom:1px solid #222A34;background:#10141A;font-size:12px;color:#95A0AE"><span>Gestor</span><span>Grupo</span>${colHead}<span style="text-align:right">Amplitud</span></div>
        ${rows || '<p class="muted" style="margin:0;padding:32px 16px;text-align:center">Ningún gestor cumple este filtro.</p>'}
        <div style="display:grid;grid-template-columns:${GRID};gap:0 8px;align-items:center;padding:12px 16px;background:#10141A;font-size:12px;color:#95A0AE"><span>Gestores con posición</span><span></span>${colFoot}<span></span></div>
      </div></div>
      <div style="display:flex;flex-wrap:wrap;gap:16px;padding:12px 16px;border-top:1px solid #222A34;font-size:12px;color:#95A0AE">
        <span style="display:inline-flex;align-items:center;gap:8px"><span class="mono" style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:20px;border-radius:4px;background:#22324A;border:1px solid #34507A;color:#B9D4FF;font-size:11px;font-weight:600">POS</span>tiene posición</span>
        <span style="display:inline-flex;align-items:center;gap:8px"><span class="mono" style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:20px;border-radius:4px;background:rgba(91,214,154,0.14);border:1px solid rgba(91,214,154,0.6);color:#5BD69A;font-size:11px;font-weight:600">+</span>abrió o aumentó (solo si lo hizo en ≥ 2 empresas a la vez)</span></div></section>
      <aside style="flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:16px">
        <div class="card" style="display:flex;flex-direction:column;gap:12px;padding:18px"><span class="muted" style="font-size:12px">Gestor seleccionado</span>
          ${sm ? `<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px"><span style="font-size:18px;font-weight:600">${esc(sm.g)}</span><span class="tag">${esc(sm.grp)}</span></div>
            <div style="display:flex;flex-direction:column;gap:4px"><span class="muted" style="font-size:12px">Posición en ${sm.held.length} de ${N}</span><span class="mono" style="font-size:14px;color:#B9D4FF">${esc(sm.held.join('  '))}</span></div>
            <div style="display:flex;flex-direction:column;gap:4px"><span class="muted" style="font-size:12px">Abrió o aumentó el último trimestre</span>
              <span class="mono" style="font-size:14px;color:${sm.up.length ? '#5BD69A' : '#95A0AE'}">${sm.up.length ? esc(sm.up.join('  ')) : 'no aumentó en ≥ 2 a la vez'}</span></div>` : '<span class="muted">Sin gestores.</span>'}</div>
        <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
          <div class="card" style="display:flex;flex-direction:column;gap:4px;padding:14px 16px"><span class="muted" style="font-size:12px">Con posición en ≥ 2</span><span class="mono" style="font-size:26px;font-weight:500">${nMulti}</span></div>
          <div class="card" style="display:flex;flex-direction:column;gap:4px;padding:14px 16px"><span class="muted" style="font-size:12px">Aumentaron en ≥ 2</span><span class="mono" style="font-size:26px;font-weight:500;color:#5BD69A">${nUp}</span></div></div>
        <div class="card" style="display:flex;flex-direction:column;gap:10px;padding:18px"><span style="font-size:13px;font-weight:600">Sectores sin convergencia</span>
          <p style="margin:0;font-size:12.5px;color:#B4BDC9">Se necesita ≥ 2 empresas del mismo sector en el universo. Estos tienen una sola:</p>
          <div style="display:flex;flex-direction:column;gap:6px">${cv.single.length ? cv.single.map((s) => `<div style="display:flex;justify-content:space-between;gap:8px;font-size:13px"><span>${esc(s.sector)}</span><span class="mono muted">${esc(s.tickers.join(' '))}</span></div>`).join('') : '<span class="muted" style="font-size:13px">Ninguno.</span>'}</div>
          <a href="#corrida" style="font-size:13px">Agregar pares del sector en una nueva corrida</a></div>
        <p class="muted" style="margin:0;font-size:12px">Pasivos (${esc(passive.slice(0, 3).join(', '))}${passive.length > 3 ? ` y ${passive.length - 3} más` : ''}) no cuentan: sus cambios reflejan flujos de índices. Cuantitativos y multiestrategia (Renaissance, Citadel, Millennium, Two Sigma, Bridgewater) se excluyen a propósito.</p>
      </aside></div></main>`;
}

// ---------------------------------------------------------------------------
// Nueva corrida
// ---------------------------------------------------------------------------
function parseUniverse() {
  const tokens = S.cfg.text.split('\n').filter((l) => l.trim().charAt(0) !== '#').join(' ').split(/[\s,;]+/).filter(Boolean).map((x) => x.toUpperCase());
  const seen = {}, good = [], bad = [];
  tokens.forEach((t) => { if (seen[t]) return; seen[t] = 1; (TICKER_RE.test(t) ? good : bad).push(t); });
  return { good, bad };
}
function thVal(k) { return S.cfg.th[k] ?? S.defaults[k] ?? ''; }

function viewCorrida() {
  const { good, bad } = parseUniverse();
  const st = S.cfg, running = !!S.run.running;
  const chips = good.map((t) => `<span class="mono" style="padding:3px 9px;border-radius:4px;font-size:12px;font-weight:600;color:#E7EAEE;border:1px solid #2A323D;background:#1A2029">${esc(t)}</span>`)
    .concat(bad.map((t) => `<span class="mono" style="padding:3px 9px;border-radius:4px;font-size:12px;font-weight:600;color:#FF9C9C;border:1px dashed rgba(255,125,125,0.6)">${esc(t)}</span>`)).join('');

  const mods = MODS.map(([k, label, desc, flag]) => {
    const on = !!st.mods[k];
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 14px;border:1px solid #1D242D;border-radius:6px;background:#10141A">
      <div style="display:flex;flex-direction:column;gap:2px;min-width:0"><span style="font-size:14px;font-weight:500">${label}</span><span class="muted" style="font-size:12px">${desc}</span>
        <span class="mono" style="font-size:11px;color:${on ? '#5BD69A' : '#F2B84B'}">${on ? 'activo' : 'se omitirá (' + flag + ')'}</span></div>
      <button type="button" aria-pressed="${on}" aria-label="${esc(label)}" data-act="mod" data-v="${k}" style="flex:none;display:inline-flex;align-items:center;width:52px;height:44px;padding:0;border:0;background:transparent;cursor:pointer">
        <span style="display:flex;align-items:center;justify-content:${on ? 'flex-end' : 'flex-start'};width:44px;height:24px;padding:3px;border-radius:12px;background:${on ? '#2E5E46' : '#1D242D'};border:1px solid ${on ? '#5BD69A' : '#3A4350'}">
          <span style="width:16px;height:16px;border-radius:8px;background:${on ? '#E7EAEE' : '#95A0AE'}"></span></span></button></div>`;
  }).join('');

  const groups = GROUPS.map((g) => `<fieldset style="margin:0;padding:14px;border:1px solid #1D242D;border-radius:6px;background:#10141A">
    <legend style="padding:0 6px;font-size:12px;font-weight:600;color:#C5CCD6">${g.label} <span class="mono muted" style="font-weight:400">${g.file}</span></legend>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">${g.items.map(([k, label, unit]) => {
      const changed = String(thVal(k)) !== String(S.defaults[k]);
      return `<div style="display:flex;flex-direction:column;gap:4px"><label for="th-${k}" style="font-size:12px;color:#B4BDC9">${label}</label>
        <div style="display:flex;align-items:center;gap:6px"><input id="th-${k}" data-th="${k}" type="number" step="any" min="0" value="${esc(thVal(k))}" style="width:100%;min-height:40px;padding:0 10px;border-radius:5px;border:1px solid ${changed ? '#F2B84B' : '#2A323D'};background:#0D1015;color:#E7EAEE;font-family:'IBM Plex Mono',monospace;font-size:13px">
        <span class="mono muted" style="font-size:12px;min-width:34px">${unit}</span></div></div>`;
    }).join('')}</div></fieldset>`).join('');

  const ua = st.ua.trim(), uaEmpty = ua === '';
  const uaOk = /^\S+(\s+\S+)*\s+\S+@\S+\.\S+$/.test(ua);
  const uaMsg = uaEmpty
    ? 'Sin definir se usa el placeholder screener-personal contacto@example.com; la SEC puede bloquear identificación falsa. El correo solo viaja en el encabezado HTTP: no se escribe en archivos ni en la caché.'
    : (uaOk ? 'Formato correcto. Se usa solo en esta sesión del servidor, no se guarda.' : 'Formato esperado: Nombre Apellido correo@dominio.com');

  const flags = MODS.filter(([k]) => !st.mods[k]).map((m) => m[3]);
  const outName = st.out.trim() || 'salida';
  const cmd = `$env:SEC_USER_AGENT = "${ua || 'Nombre Apellido correo@dominio.com'}"\npython screener.py ${good.join(' ') || '[TICKERS]'} --out ${outName}${flags.length ? ' ' + flags.join(' ') : ''}`;
  const lo = good.length * 30 + (st.mods.f13 ? 240 : 0), hi = good.length * 60 + (st.mods.f13 ? 240 : 0);
  const est = `${Math.round(lo / 60)}–${Math.round(hi / 60)}`;
  const why = `${good.length} × 30–60 s por ticker${st.mods.f13 ? ' + ~4 min para descargar los 78 archivos 13F.' : '.'}`;

  const pipe = PIPE.map(([mod, fn, d], i) => {
    const on = mod === null || st.mods[mod];
    return `<div style="display:grid;grid-template-columns:24px minmax(0,1fr);gap:10px;padding:8px 0;border-bottom:1px solid #1D242D">
      <span class="mono" style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:11px;font-size:11px;font-weight:600;color:${on ? '#8DBBFF' : '#95A0AE'};border:1px solid ${on ? '#34507A' : '#3A4350'}">${i + 1}</span>
      <div style="display:flex;flex-direction:column;gap:1px"><span class="mono" style="font-size:12.5px;color:${on ? '#E7EAEE' : '#95A0AE'};text-decoration:${on ? 'none' : 'line-through'}">${fn}</span><span class="muted" style="font-size:12px">${on ? d : 'omitido'}</span></div></div>`;
  }).join('');
  const outs = [`${outName}_resumen.csv`, `${outName}_detalle.csv`, `${outName}.md`, 'consola'].map((o) => `<span class="mono" style="padding:3px 8px;border-radius:4px;border:1px solid #2A323D;font-size:11px;color:#C5CCD6">${esc(o)}</span>`).join('');

  const cant = good.length === 0 || running;
  const pct = S.run.total ? Math.min(100, Math.round(S.run.step / S.run.total * 100)) : 0;
  const runLabel = running ? `Ejecutando… ${S.run.step}/${S.run.total}` : `Ejecutar sobre ${good.length} tickers`;
  const progress = running
    ? `<div class="card" style="padding:14px 16px;display:flex;flex-direction:column;gap:8px" role="status" aria-live="polite">
        <div style="height:6px;border-radius:3px;background:#1D242D;overflow:hidden"><div style="width:${pct}%;height:6px;background:#6CA8FF"></div></div>
        <span class="mono muted" style="font-size:12px">${esc(S.run.msg)}</span></div>` : '';
  const err = (S.runErr || S.run.error)
    ? `<div role="alert" style="padding:12px 16px;border:1px solid rgba(255,125,125,0.4);border-radius:8px;background:rgba(255,125,125,0.06);font-size:13px;color:#FF9C9C">${esc(S.runErr || S.run.error)}</div>` : '';

  return `<main>
    <div style="display:flex;flex-direction:column;gap:6px"><h1>Configurar corrida</h1>
      <p style="margin:0;font-size:13.5px;color:#B4BDC9">Universo, módulos y umbrales. Ninguna fuente requiere cuenta ni API key.</p></div>
    <div style="display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start">
    <div style="flex:999 1 640px;min-width:0;display:flex;flex-direction:column;gap:16px">
      <section class="card" style="display:flex;flex-direction:column;gap:12px;padding:20px">
        <div style="display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px"><h2 style="margin:0;font-size:15px;font-weight:600">1 · Universo de tickers</h2>
          <span class="mono muted" style="font-size:12px">${good.length} válidos · líneas con # se ignoran · máx. 40</span></div>
        <label for="tk" class="muted" style="font-size:12px">Uno por línea, o separados por espacio o coma</label>
        <textarea id="tk" rows="6" spellcheck="false" style="width:100%;padding:12px;border-radius:6px;border:1px solid #2A323D;background:#0D1015;color:#E7EAEE;font-family:'IBM Plex Mono',monospace;font-size:13px;line-height:1.6;resize:vertical">${esc(st.text)}</textarea>
        <div style="display:flex;flex-wrap:wrap;gap:6px">${chips}</div>
        ${bad.length ? `<p style="margin:0;font-size:12px;color:#FF9C9C">No parecen tickers válidos: ${esc(bad.join(', '))}</p>` : ''}
        <p class="muted" style="margin:0;font-size:12px">La convergencia 13F solo compara empresas del mismo sector dentro de este universo: incluye pares si quieres esa lectura.</p></section>
      <section class="card" style="display:flex;flex-direction:column;gap:12px;padding:20px"><h2 style="margin:0;font-size:15px;font-weight:600">2 · Módulos</h2>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 14px;border:1px solid #1D242D;border-radius:6px;background:#10141A">
            <div style="display:flex;flex-direction:column;gap:2px"><span style="font-size:14px;font-weight:500">Cinco filtros fundamentales</span><span class="muted" style="font-size:12px">yfinance + EDGAR 10-K · siempre activo</span></div>
            <span class="mono muted" style="font-size:12px">base</span></div>${mods}</div></section>
      <section class="card" style="display:flex;flex-direction:column;gap:14px;padding:20px">
        <div style="display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px"><h2 style="margin:0;font-size:15px;font-weight:600">3 · Umbrales</h2>
          <button type="button" data-act="reset" style="min-height:44px;padding:0 12px;border-radius:6px;border:1px solid #2A323D;background:transparent;color:#C5CCD6;font-size:13px;cursor:pointer">Restablecer valores</button></div>
        <p class="muted" style="margin:0;font-size:12px">Puntos de partida razonables, no óptimos calibrados. Los cambiados se marcan.</p>${groups}</section>
      <section class="card" style="display:flex;flex-direction:column;gap:12px;padding:20px"><h2 style="margin:0;font-size:15px;font-weight:600">4 · Salida e identificación ante la SEC</h2>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px">
          <div style="display:flex;flex-direction:column;gap:4px"><label for="out" style="font-size:12px;color:#B4BDC9">Nombre de salida (--out)</label>
            <input id="out" type="text" value="${esc(st.out)}" style="min-height:40px;padding:0 10px;border-radius:5px;border:1px solid #2A323D;background:#0D1015;color:#E7EAEE;font-family:'IBM Plex Mono',monospace;font-size:13px"></div>
          <div style="display:flex;flex-direction:column;gap:4px"><label for="ua" style="font-size:12px;color:#B4BDC9">SEC_USER_AGENT</label>
            <input id="ua" type="text" value="${esc(st.ua)}" placeholder="Nombre Apellido correo@dominio.com" autocomplete="off" style="min-height:40px;padding:0 10px;border-radius:5px;border:1px solid ${uaEmpty ? '#2A323D' : (uaOk ? '#5BD69A' : '#F2B84B')};background:#0D1015;color:#E7EAEE;font-family:'IBM Plex Mono',monospace;font-size:13px"></div></div>
        <p style="margin:0;font-size:12px;color:${uaEmpty || uaOk ? '#95A0AE' : '#F2B84B'}">${esc(uaMsg)}</p></section>
    </div>
    <aside style="flex:1 1 380px;min-width:0;display:flex;flex-direction:column;gap:16px">
      <section class="card" style="display:flex;flex-direction:column;gap:10px;padding:18px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px"><h2 class="h2s">Comando · PowerShell</h2>
          <button type="button" data-act="copy" style="min-height:36px;padding:0 10px;border-radius:5px;border:1px solid #2A323D;background:transparent;color:#C5CCD6;font-size:12px;cursor:pointer">${S.copied ? 'Copiado' : 'Copiar'}</button></div>
        <pre class="mono" style="margin:0;padding:12px;border-radius:6px;background:#0D1015;border:1px solid #1D242D;font-size:12px;line-height:1.6;color:#C5CCD6;white-space:pre-wrap;word-break:break-word">${esc(cmd)}</pre></section>
      <section class="card" style="display:flex;flex-direction:column;gap:10px;padding:18px"><h2 class="h2s">Estimación</h2>
        <div style="display:flex;align-items:baseline;gap:8px"><span class="mono" style="font-size:28px;font-weight:500">${est}</span><span class="muted" style="font-size:13px">min, primera corrida</span></div>
        <p class="muted" style="margin:0;font-size:12px">${why} Con caché en .cache_edgar/ las repeticiones son casi instantáneas (ocupa 350+ MB). EDGAR: 5 peticiones/s.</p></section>
      <section class="card" style="display:flex;flex-direction:column;gap:4px;padding:18px"><h2 class="h2s" style="margin:0 0 8px">Orden de ejecución</h2>${pipe}
        <div style="display:flex;flex-wrap:wrap;gap:6px;padding-top:10px">${outs}</div></section>
      ${progress}${err}
      <button type="button" data-act="run" ${cant ? 'disabled' : ''} style="min-height:52px;padding:0 20px;border-radius:8px;border:0;background:${cant ? '#1D242D' : '#6CA8FF'};color:${cant ? '#95A0AE' : '#0D1015'};font-size:15px;font-weight:600;cursor:${cant ? 'not-allowed' : 'pointer'}">${runLabel}</button>
    </aside></div></main>`;
}

// ---------------------------------------------------------------------------
// Render y eventos
// ---------------------------------------------------------------------------
const app = document.getElementById('app');
const VIEWS = { resumen: viewResumen, detalle: viewDetalle, convergencia: viewConvergencia, corrida: viewCorrida };

function render() {
  const a = document.activeElement;
  const key = a && (a.id ? '#' + a.id : (a.dataset && a.dataset.act ? `[data-act="${a.dataset.act}"][data-v="${a.dataset.v}"]` : ''));
  let s = null, e = null;
  if (a && a.id && 'selectionStart' in a) { try { s = a.selectionStart; e = a.selectionEnd; } catch (_) { /* tipos sin selección */ } }
  const y = window.scrollY;
  app.innerHTML = header() + VIEWS[S.view]();
  document.title = 'Screener · ' + (NAV.find((n) => n[0] === S.view) || [, ''])[1];
  if (key) {
    const n = app.querySelector(key.startsWith('#') ? key : key);
    if (n) { n.focus({ preventScroll: true }); if (s !== null) { try { n.setSelectionRange(s, e); } catch (_) { /* number inputs */ } } }
  }
  window.scrollTo(0, y);
}

function go(view) {
  S.view = VIEWS[view] ? view : 'resumen';
  if (location.hash !== '#' + S.view) history.replaceState(null, '', '#' + S.view);
  window.scrollTo(0, 0);
  render();
}

async function loadState() {
  const r = await fetch('/api/state');
  const j = await r.json();
  S.data = j.data; S.run = j.run; S.defaults = j.defaults;
  if (!S.cfg.ua && j.ua_env) S.cfg.ua = j.ua_env;
  if (!Object.keys(S.cfg.th).length) S.cfg.th = Object.assign({}, S.defaults);
  else S.cfg.th = Object.assign({}, S.defaults, S.cfg.th);
}

async function poll() {
  clearTimeout(S.timer);
  try {
    S.run = await (await fetch('/api/progress')).json();
  } catch (e) { S.run = { running: false, error: 'se perdió la conexión con el servidor' }; }
  if (S.run.running) { render(); S.timer = setTimeout(poll, 1000); return; }
  await loadState();
  if (!S.run.error) { S.sel = null; S.det = null; S.cv.sector = null; S.cv.sel = null; go('resumen'); } else render();
}

async function startRun() {
  const { good } = parseUniverse();
  S.runErr = '';
  try {
    const r = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: good, modules: S.cfg.mods, th: S.cfg.th, out: S.cfg.out, ua: S.cfg.ua }),
    });
    const j = await r.json();
    if (!r.ok) { S.runErr = j.error || 'no se pudo iniciar la corrida'; render(); return; }
  } catch (e) { S.runErr = 'no se pudo contactar al servidor'; render(); return; }
  S.run = { running: true, step: 0, total: 1, msg: 'iniciando', error: '' };
  render();
  poll();
}

app.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-act]');
  if (!t || t.disabled) return;
  const v = t.dataset.v;
  switch (t.dataset.act) {
    case 'nav': go(v); break;
    case 'sel': S.sel = v; render(); break;
    case 'open': S.det = v; go('detalle'); break;
    case 'filter': S.filter = v; render(); break;
    case 'sort': S.sort = v; render(); break;
    case 'det': S.det = v; render(); break;
    case 'grp': S.grp = v; render(); break;
    case 'cvsel': S.cv.sel = v; render(); break;
    case 'cvmode': S.cv.mode = v; render(); break;
    case 'cvgrp': S.cv.grp = v; render(); break;
    case 'cvsector': S.cv.sector = v; S.cv.sel = null; render(); break;
    case 'mod': S.cfg.mods[v] = !S.cfg.mods[v]; saveDraft(); render(); break;
    case 'reset': S.cfg.th = Object.assign({}, S.defaults); saveDraft(); render(); break;
    case 'copy': {
      const pre = app.querySelector('pre');
      if (pre && navigator.clipboard) navigator.clipboard.writeText(pre.textContent).then(() => { S.copied = true; render(); setTimeout(() => { S.copied = false; render(); }, 1500); }).catch(() => {});
      break;
    }
    case 'run': startRun(); break;
  }
});
app.addEventListener('input', (ev) => {
  const t = ev.target;
  if (t.id === 'tk') { S.cfg.text = t.value; saveDraft(); render(); }
  else if (t.id === 'out') { S.cfg.out = t.value; saveDraft(); render(); }
  else if (t.id === 'ua') { S.cfg.ua = t.value; render(); }
  else if (t.dataset.th) { S.cfg.th[t.dataset.th] = t.value; saveDraft(); }
});
app.addEventListener('change', (ev) => { if (ev.target.dataset && ev.target.dataset.th) render(); });
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (VIEWS[v] && v !== S.view) go(v); });

(async function init() {
  loadDraft();
  try { await loadState(); } catch (e) { app.innerHTML = '<main><p>No se pudo contactar al servidor.</p></main>'; return; }
  const h = location.hash.slice(1);
  S.view = VIEWS[h] ? h : (S.data ? 'resumen' : 'corrida');
  render();
  if (S.run.running) poll();
})();
