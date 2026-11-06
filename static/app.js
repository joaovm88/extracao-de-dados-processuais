const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const enc = encodeURIComponent;
const json = (m, b) => ({ method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b) });
const api = async (url, opt) => {
  const r = await fetch(url, opt); let j = {};
  try { j = await r.json(); } catch (_) { /* resposta sem JSON */ }
  if (!r.ok) { const d = j.detail; throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Falha HTTP ${r.status}`); }
  return j;
};
const store = { get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* sem armazenamento */ } } };
function toast(msg, kind = '') { const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg; $('toasts').appendChild(t); setTimeout(() => t.remove(), kind === 'err' ? 6000 : 2600); }

/* ---------- estado ---------- */
let caseId = null, data = null, dataKey = '', aba = 'execucoes_fiscais', sel = null, timer = null, schema = {}, tplState = {ativo: false}, picked = [], dragging = false;
let panelOpen = innerWidth > 900 ? store.get('painel') !== '0' : false;
let filtro = {q: '', st: ''};
// tamanhos ajustáveis (por caso, no navegador): larguras por aba/coluna, alturas por processo e densidade das linhas
let tam = {larg: {}, alt: {}, dens: 'normal'};
const defW = k => ({__first: 230, observacoes: 320, corresponsaveis: 380, inscricoes: 220, somatorio: 200}[k] ?? 150);
const wOf = (a, k) => tam.larg[a]?.[k] ?? defW(k);
function loadTam() { try { const t = JSON.parse(store.get('tam:' + caseId) || '{}'); tam = {larg: t.larg || {}, alt: t.alt || {}, dens: t.dens || 'normal'}; } catch (_) { tam = {larg: {}, alt: {}, dens: 'normal'}; } }
const saveTam = () => store.set('tam:' + caseId, JSON.stringify(tam));
const LONG = ['observacoes', 'corresponsaveis'];  // colunas de texto longo: abrem numa janela para ler/editar tudo
const chats = {};  // histórico do assistente por processo (só nesta sessão do navegador)
const ESTADO = {pronto: ['ok', 'pronto'], extraindo: ['warn', 'extraindo…'], aguardando: ['warn', 'aguardando PDFs'], desatualizado: ['warn', 'novas partes'], erro: ['err', 'erro'], sem_pdf: ['', 'sem PDF']};
const DST = {queued: ['warn', 'na fila'], processing: ['warn', 'processando'], done: ['ok', 'lido'], error: ['err', 'erro']};
const MODELO = {execucoes_fiscais: 'Execução fiscal', outras_acoes: 'Outras ações', recursos: 'Recursos'};
const norm = t => String(t ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const editable = l => ['pronto', 'desatualizado'].includes(l.estado);
const row = n => data?.linhas.find(l => l.numero === n);

/* ---------- casos ---------- */
async function loadCases(selectId) {
  const cs = await api('/api/cases');
  $('cases').innerHTML = cs.map(c => `<li tabindex="0" data-id="${c.id}">${esc(c.nome)}</li>`).join('') || '<li style="cursor:default">Nenhum caso ainda. Crie o primeiro acima.</li>';
  $('case-select').innerHTML = cs.map(c => `<option value="${c.id}" ${c.id === (selectId || caseId) ? 'selected' : ''}>${esc(c.nome)}</option>`).join('');
  document.querySelectorAll('#cases li[data-id]').forEach(li => { li.onclick = () => openCase(li.dataset.id); li.onkeydown = e => e.key === 'Enter' && openCase(li.dataset.id); });
}
async function openCase(id) {
  caseId = id; loadTam(); sel = null; data = null; dataKey = ''; picked = []; renderPicked(); aba = 'execucoes_fiscais';
  $('welcome').classList.add('hidden'); $('work').classList.remove('hidden'); $('case-switch').hidden = false;
  ['btn-upload', 'btn-tpl'].forEach(b => $(b).disabled = false); $('export').href = `/api/cases/${id}/spreadsheet`; $('export').removeAttribute('aria-disabled');
  $('case-select').value = id; applyPanel();
  tplState = await api(`/api/cases/${id}/template`).catch(() => ({ativo: false})); showTemplate(tplState);
  await refresh(true); clearInterval(timer); timer = setInterval(refresh, 2500);
}
function goHome(e) {
  e && e.preventDefault(); clearInterval(timer); caseId = null; data = null;
  $('work').classList.add('hidden'); $('welcome').classList.remove('hidden'); $('case-switch').hidden = true;
  ['btn-upload', 'btn-tpl'].forEach(b => $(b).disabled = true); $('export').setAttribute('aria-disabled', 'true'); loadCases();
}
async function createCase(nome) {
  const c = await api('/api/cases', json('POST', {nome})); await loadCases(c.id); openCase(c.id);
}
$('home').onclick = goHome;
$('case-select').onchange = e => openCase(e.target.value);
$('new-case').onsubmit = async e => { e.preventDefault(); try { await createCase($('case-name').value); $('case-name').value = ''; } catch (err) { toast(err.message, 'err'); } };
$('case-new').onclick = () => { $('case-name2').value = ''; $('dlg-case').showModal(); $('case-name2').focus(); };
$('case-ok').onclick = async () => { const v = $('case-name2').value.trim(); if (!v) return; try { await createCase(v); $('dlg-case').close(); } catch (e) { toast(e.message, 'err'); } };
$('case-name2').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('case-ok').click(); } };

/* ---------- atualização dos dados ---------- */
async function refresh(force) {
  if (!caseId) return;
  const el = document.activeElement;
  if (!force && (el?.closest('#grid') && ['INPUT', 'SELECT'].includes(el.tagName) || document.querySelector('dialog[open]') || dragging)) return;  // não atrapalha quem está digitando
  let d; try { d = await api(`/api/cases/${caseId}/sheet`); } catch (_) { return; }
  const k = JSON.stringify(d); if (k === dataKey && !force) return;
  dataKey = k; data = d; renderAll();
}
function renderAll() { renderTabs(); renderGrid(); renderToolbar(); renderAssistInfo(); renderDocs(); }

/* ---------- abas, barra de ferramentas, banners ---------- */
function renderTabs() {
  $('tabs').innerHTML = data.abas.map(a => `<button role="tab" data-a="${a.key}" class="${a.key === aba ? 'on' : ''}" aria-selected="${a.key === aba}">${esc(a.titulo)}<b>${a.n}</b></button>`).join('');
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => { aba = b.dataset.a; renderTabs(); renderGrid(); });
}
function renderToolbar() {
  const busy = data.documentos.filter(d => ['queued', 'processing'].includes(d.status)).length, ext = data.linhas.filter(l => l.estado === 'extraindo').length;
  $('pill-busy').hidden = !(busy || ext); $('pill-busy').textContent = busy ? `⏳ ${busy} PDF(s) em leitura` : '✨ IA extraindo dados…';
  $('docs-n').textContent = data.documentos.length ? `(${data.documentos.length})` : '';
  const orphans = data.documentos.filter(d => !d.numero_processo && !['queued', 'processing'].includes(d.status));
  const errs = data.documentos.filter(d => d.status === 'error');
  $('banner').hidden = !(orphans.length || errs.length);
  $('banner').innerHTML = [errs.length ? `${errs.length} PDF(s) com erro.` : '', orphans.length ? `${orphans.length} PDF(s) sem nº de processo identificado (renomeie com o nº CNJ e reenvie).` : '']
    .filter(Boolean).join(' ') + ' <a href="#" id="banner-docs">Ver PDFs</a>';
  const b = $('banner-docs'); if (b) b.onclick = e => { e.preventDefault(); openDocs(); };
}

/* ---------- filtro das linhas ---------- */
function passa(l) {
  if (filtro.st) {
    const ed = editable(l), ok = {revisar: ed && !l.reviewed, revisado: ed && l.reviewed, erro: l.estado === 'erro',
      andamento: ['extraindo', 'aguardando'].includes(l.estado) || l.docs.some(d => ['queued', 'processing'].includes(d.status)), sem_pdf: l.estado === 'sem_pdf'}[filtro.st];
    if (!ok) return false;
  }
  const termos = norm(filtro.q).split(/\s+/).filter(Boolean); if (!termos.length) return true;
  const palha = norm(`${l.numero} ${Object.values(l.valores).join(' ')}`); return termos.every(t => palha.includes(t));
}
const passaItem = i => { const termos = norm(filtro.q).split(/\s+/).filter(Boolean); const p = norm(Object.values(i).join(' ')); return termos.every(t => p.includes(t)); };
function updateFilterUI(shown, total) {
  const ativo = !!(filtro.q.trim() || filtro.st); $('f-clear').hidden = !ativo; $('f-count').textContent = ativo ? `Mostrando ${shown} de ${total}` : `${total} linha(s)`;
}
$('f-q').oninput = e => { filtro.q = e.target.value; if (data) renderGrid(); };
$('f-st').onchange = e => { filtro.st = e.target.value; if (data) renderGrid(); };
$('f-clear').onclick = () => { filtro = {q: '', st: ''}; $('f-q').value = ''; $('f-st').value = ''; renderGrid(); $('f-q').focus(); };
document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.querySelector('dialog[open]') && caseId) { e.preventDefault(); $('f-q').focus(); } });

/* ---------- planilha viva ---------- */
function gridHead(keys, heads) {  // <colgroup> com as larguras + cabeçalho com alça de redimensionar
  const ws = keys.map(k => wOf(aba, k)), t = $('grid');
  t.style.width = ws.reduce((x, y) => x + y, 0) + 'px'; t.className = 'dens-' + tam.dens;
  return `<colgroup>${keys.map((k, i) => `<col data-k="${k}" style="width:${ws[i]}px">`).join('')}</colgroup><thead><tr>${heads.map((h, i) =>
    `<th${h.title ? ` title="${esc(h.title)}"` : ''}>${esc(h.text)}<span class="rz" data-rz="${keys[i]}" title="Arraste para ajustar a largura · duplo clique restaura"></span></th>`).join('')}</tr></thead>`;
}
function renderGrid() {
  const a = data.abas.find(x => x.key === aba), table = $('grid');
  const todas = data.linhas.filter(l => l.aba === aba), linhas = todas.filter(passa);
  $('grid-empty').hidden = true; applyDens();
  if (aba === 'bens' || aba === 'fraude') {
    const todos = aba === 'bens' ? data.bens : data.fraudes, items = todos.filter(passaItem); updateFilterUI(items.length, todos.length);
    table.innerHTML = gridHead(['__first', ...a.colunas.map(c => c.key)], [{text: 'Processo'}, ...a.colunas.map(c => ({text: c.titulo}))]) + `<tbody>${items.map(i =>
      `<tr><td class="first"><div class="cc"><span class="num">${esc(i.numero)}</span></div></td>${a.colunas.map(c => `<td class="c ro"><div class="cc">${esc(i[c.key] ?? '')}</div></td>`).join('')}</tr>`).join('')}</tbody>`;
    if (!items.length) emptyGrid(todos.length ? 'Nenhum item corresponde ao filtro' : aba === 'bens' ? 'Nenhum bem extraído ainda' : 'Nenhuma alegação de fraude', todos.length ? 'Ajuste ou limpe o filtro acima.' : 'Os itens aparecem aqui quando a IA os encontra nos processos. Esta aba é somente leitura.');
    return;
  }
  updateFilterUI(linhas.length, todas.length);
  const cols = a.colunas.filter(c => c.key !== 'numero');
  table.innerHTML = gridHead(['__first', ...cols.map(c => c.key)], [{text: 'Processo'}, ...cols.map(c => ({text: c.titulo, title: c.titulo}))]) + `<tbody>${linhas.map(l => {
    const [cls, txt] = ESTADO[l.estado], ed = editable(l), waiting = ['extraindo', 'aguardando'].includes(l.estado);
    const rc = (l.numero === sel ? 'sel ' : '') + (l.estado === 'erro' ? 'bad' : ed && l.reviewed ? 'okrow' : ed ? 'pend' : '');
    const cut = l.medidas?.contexto?.cortado ? `<span class="tag warn" title="A IA viu ${l.medidas.contexto.paginas_enviadas} de ${l.medidas.contexto.paginas_total} páginas (limite de contexto)">cortado</span>` : '';
    const warn = l.citacoes_invalidas.length ? `<span class="tag err" title="Citações que não existem no PDF: ${esc(l.citacoes_invalidas.join('; '))}">⚠ citação</span>` : '';
    const h = tam.alt[l.numero];
    return `<tr data-n="${esc(l.numero)}" class="${rc}"${h ? ` style="--rh:${h}px"` : ''}><td class="first"><div class="cc"><span class="num">${esc(l.numero)}</span>
      <div class="sub"><span class="tag ${cls}">${txt}</span>${cut}${warn}${ed ? `<label class="rev" title="Marcar como revisado"><input type="checkbox" data-rev ${l.reviewed ? 'checked' : ''}> <span>revisado</span></label>` : ''}</div></div>
      <span class="rzr" title="Arraste para ajustar a altura da linha · duplo clique restaura"></span></td>
      ${cols.map(c => cell(l, c, ed, waiting)).join('')}</tr>`;
  }).join('')}</tbody>`;
  if (!linhas.length) emptyGrid(todas.length ? 'Nenhum processo corresponde ao filtro' : 'Nenhum processo nesta aba', todas.length ? 'Ajuste ou limpe o filtro acima.' : 'Importe a planilha-modelo e envie PDFs (botão "Upload de Processo" no topo, ou arraste os arquivos para cá).');
}
function emptyGrid(t, p) { const g = $('grid-empty'); g.hidden = false; g.innerHTML = `<b>${esc(t)}</b>${esc(p)}`; }
function cell(l, c, ed, waiting) {
  const v = l.valores[c.key] ?? '', tip = l.citacoes[c.key] ? ` title="Origem: ${esc(l.citacoes[c.key])}"` : '';
  const W = (inner, cls = '') => `<td${cls ? ` class="${cls}"` : ''}${tip}><div class="cc">${inner}</div></td>`;
  if (c.fonte !== 'ai') return W(esc(v), 'c ro');  // capa (Mcfly) e campos do sistema: somente leitura
  if (!ed) return W(waiting ? '<div class="shim"></div>' : '<div class="wait">—</div>');
  if (c.key === 'corresponsaveis') {  // lista: uma pessoa por linha, com CPF/CNPJ; o resto fica na janela
    const ls = String(v).split('\n').filter(Boolean);
    return W(`<div class="clamp lista" data-long="${c.key}" tabindex="0" role="button" title="Clique para ler e editar a lista inteira">${ls.slice(0, 3).map(x => `<div class="li">${esc(x)}</div>`).join('')}${ls.length > 3 ? `<div class="mais">+${ls.length - 3} na lista…</div>` : ''}</div>`);
  }
  if (LONG.includes(c.key)) return W(`<div class="clamp" data-long="${c.key}" tabindex="0" role="button" title="Clique para ler e editar tudo">${esc(v)}</div>`);
  if (c.opcoes) return W(`<select data-k="${c.key}"><option value=""></option>${c.opcoes.map(o => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`);
  return W(`<input type="text" data-k="${c.key}" value="${esc(v)}">`);
}

/* ---------- ajustar tamanhos: arrastar a borda da coluna ou da linha ---------- */
function applyDens() {
  $('grid').className = 'dens-' + tam.dens;
  document.querySelectorAll('#dens [data-d]').forEach(b => b.classList.toggle('on', b.dataset.d === tam.dens));
}
function drag(e, onMove, onEnd) {
  e.preventDefault(); e.stopPropagation(); dragging = true; document.body.classList.add('resizing');
  const mv = ev => onMove(ev), up = () => { window.removeEventListener('pointermove', mv); dragging = false; document.body.classList.remove('resizing'); onEnd(); };
  window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up, {once: true});
}
$('grid').addEventListener('pointerdown', e => {
  const h = e.target.closest('.rz'), r = e.target.closest('.rzr');
  if (h) {  // largura da coluna
    const k = h.dataset.rz, col = $('grid').querySelector(`col[data-k="${k}"]`), x0 = e.clientX, w0 = col.getBoundingClientRect().width, t0 = parseFloat($('grid').style.width);
    h.classList.add('on');
    drag(e, ev => { const w = Math.round(Math.max(70, Math.min(900, w0 + ev.clientX - x0))); col.style.width = w + 'px'; $('grid').style.width = (t0 - w0 + w) + 'px'; (tam.larg[aba] ||= {})[k] = w; },
      () => { h.classList.remove('on'); saveTam(); });
  } else if (r) {  // altura da linha
    const tr = r.closest('tr'), n = tr.dataset.n, y0 = e.clientY, h0 = tr.getBoundingClientRect().height;
    drag(e, ev => { const alt = Math.round(Math.max(40, Math.min(700, h0 + ev.clientY - y0))); tr.style.setProperty('--rh', alt + 'px'); tam.alt[n] = alt; }, saveTam);
  }
});
$('grid').addEventListener('dblclick', e => {  // duplo clique na alça: volta ao tamanho padrão
  const h = e.target.closest('.rz'), r = e.target.closest('.rzr');
  if (h && tam.larg[aba]) { delete tam.larg[aba][h.dataset.rz]; saveTam(); renderGrid(); }
  if (r) { delete tam.alt[r.closest('tr').dataset.n]; saveTam(); renderGrid(); }
});
$('dens').onclick = e => { const b = e.target.closest('[data-d]'); if (!b) return; tam.dens = b.dataset.d; saveTam(); applyDens(); };
$('tam-reset').onclick = () => { tam = {larg: {}, alt: {}, dens: 'normal'}; saveTam(); if (data) renderGrid(); toast('Tamanhos de colunas e linhas restaurados'); };

async function save(numero, key, value, td) {
  const l = row(numero); if (!l) return;
  const prev = l.valores[key] ?? '', val = (value ?? '').trim();
  if (val === String(prev)) return;
  td && td.classList.add('saving');
  const body = key === 'observacoes' ? {observacoes: value, reviewed: l.reviewed} : {fields: {[key]: val || null}, reviewed: l.reviewed};
  try {
    await api(`/api/cases/${caseId}/processes/${enc(numero)}/review`, json('PUT', body));
    l.valores[key] = val; dataKey = JSON.stringify(data); toast('Salvo', 'ok');
    if (td) { td.classList.replace('saving', 'saved'); setTimeout(() => td.classList.remove('saved'), 900); }
    if (LONG.includes(key)) renderGrid();
  } catch (e) { toast(e.message, 'err'); td && td.classList.remove('saving'); renderGrid(); }
}
$('grid').addEventListener('change', e => {
  const tr = e.target.closest('tr'); if (!tr) return;
  if (e.target.matches('[data-rev]')) return setReviewed(tr.dataset.n, e.target.checked);
  if (e.target.matches('[data-k]')) save(tr.dataset.n, e.target.dataset.k, e.target.value, e.target.closest('td'));
});
$('grid').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('input[data-k]')) e.target.blur();
  if (e.key === 'Escape' && e.target.matches('input[data-k]')) { e.target.value = row(e.target.closest('tr').dataset.n)?.valores[e.target.dataset.k] ?? ''; e.target.blur(); }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-long]')) { e.preventDefault(); openLong(e.target.closest('tr').dataset.n, e.target.dataset.long); }
});
$('grid').addEventListener('click', e => {
  if (e.target.closest('.rz,.rzr')) return;
  const tr = e.target.closest('tbody tr'); if (!tr || !tr.dataset.n) return;
  const lg = e.target.closest('[data-long]'); if (lg) openLong(tr.dataset.n, lg.dataset.long);
  if (e.target.closest('input[type=checkbox],label')) return;
  select(tr.dataset.n);
});
async function setReviewed(numero, v) {
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/review`, json('PUT', {reviewed: v})); row(numero).reviewed = v; dataKey = JSON.stringify(data); toast(v ? 'Marcado como revisado' : 'Marcado como a revisar', 'ok'); renderGrid(); renderAssistInfo(); }
  catch (e) { toast(e.message, 'err'); renderGrid(); }
}
/* texto longo (observações, corresponsáveis): janela para ler e editar tudo */
let longFor = null;
function openLong(numero, key) {
  const l = row(numero); if (!l || !editable(l)) return; const col = data.abas.flatMap(a => a.colunas).find(c => c.key === key);
  longFor = {numero, key}; $('notes-title').textContent = col ? col.titulo : 'Texto'; $('notes-sub').textContent = numero; $('notes-text').value = l.valores[key] ?? '';
  $('notes-cit').textContent = l.citacoes[key] ? `Origem: ${l.citacoes[key]}` : ''; $('notes-fmt').hidden = key !== 'corresponsaveis'; $('dlg-notes').showModal(); $('notes-text').focus();
}
$('notes-fmt').onclick = async () => {
  try { const r = await api('/api/format/corresponsaveis', json('POST', {texto: $('notes-text').value})); $('notes-text').value = r.texto; toast('Lista padronizada (clique em Salvar para gravar)'); } catch (e) { toast(e.message, 'err'); }
};
$('notes-save').onclick = async () => { const f = longFor; await save(f.numero, f.key, $('notes-text').value, null); $('dlg-notes').close(); };

/* ---------- painel do assistente (retrátil) ---------- */
function applyPanel() {
  $('work').classList.toggle('collapsed', !panelOpen); $('assist-open').hidden = panelOpen; store.set('painel', panelOpen ? '1' : '0');
}
$('assist-close').onclick = () => { panelOpen = false; applyPanel(); };
$('assist-open').onclick = () => { panelOpen = true; applyPanel(); };
function select(numero) {
  sel = numero; document.querySelectorAll('#grid tbody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.n === numero));
  if (!panelOpen) { panelOpen = true; applyPanel(); }
  $('assist-empty').hidden = true; $('assist-body').hidden = false; $('assist-sub').textContent = numero;
  renderAssistInfo(); renderChat();
  if (innerWidth > 900) $('q').focus({preventScroll: true});
}
function renderAssistInfo() {
  const l = sel && row(sel); if (!l) { $('assist-empty').hidden = false; $('assist-body').hidden = true; $('assist-sub').textContent = 'Selecione uma linha'; return; }
  const [cls, txt] = ESTADO[l.estado], ed = editable(l);
  const pdfs = l.docs.map(d => { const [dc, dt] = DST[d.status] || ['', d.status];
    return `<div class="pdf"><span class="nm" title="${esc(d.filename)}">${esc(d.filename)}</span><span class="tag ${dc}">${dt}</span>
      ${d.error ? `<div class="er">${esc(d.error)}</div>` : ''}<button class="icon-btn" data-retry="${d.id}" title="Reprocessar este PDF" aria-label="Reprocessar">⟳</button><button class="icon-btn" data-del="${d.id}" title="Excluir este PDF" aria-label="Excluir">🗑</button></div>`; }).join('');
  $('assist-info').innerHTML = `<div class="info-top"><span class="num">${esc(l.numero)}</span><span class="tag ${cls}">${txt}</span>
      ${ed ? `<span class="tag ${l.reviewed ? 'ok' : 'blue'}">${l.reviewed ? 'revisado' : 'a revisar'}</span>` : ''}
      ${l.modelo ? `<span class="chip">${esc(MODELO[l.modelo] || l.modelo)}</span>` : ''}${l.sistema ? `<span class="chip">${esc(l.sistema)}</span>` : ''}${l.paginas ? `<span class="chip">${l.paginas} págs.</span>` : ''}${l.medidas ? `<span class="chip" title="Páginas enviadas à IA e tokens da extração">IA viu ${l.medidas.contexto.paginas_enviadas}/${l.medidas.contexto.paginas_total} págs. · ${fmtN(l.medidas.tokens?.entrada)} tokens</span>` : ''}</div>
    ${l.error ? `<div class="alert err"><b>Erro na extração.</b> ${esc(l.error)}${l.docs.length ? `<br><button class="btn sm" data-again>Tentar novamente</button>` : ''}</div>` : ''}
    ${l.citacoes_invalidas.length ? `<div class="alert err"><b>Citações que não existem no PDF</b> (conferir): ${esc(l.citacoes_invalidas.join('; '))}</div>` : ''}
    ${l.modo === 'sem_llm' ? '<div class="alert warn">A IA não estava configurada: esta extração está vazia.</div>' : ''}
    <div class="acts">${ed ? `<button class="btn sm primary" data-toggle-rev>${l.reviewed ? '↺ Marcar como a revisar' : '✓ Marcar como revisado'}</button>` : ''}
      ${l.docs.length ? `<button class="btn sm" data-reextract title="Refaz a extração com todas as partes (descarta edições)">⟳ Refazer extração</button>` : ''}</div>
    <details class="pdfs" ${l.docs.some(d => d.status === 'error') ? 'open' : ''}><summary>PDFs deste processo (${l.docs.length})</summary>${pdfs || '<p class="muted">Nenhum PDF enviado para este processo.</p>'}</details>`;
  const q = (s) => $('assist-info').querySelector(s);
  q('[data-again]') && (q('[data-again]').onclick = () => reextract(l.numero, false));
  q('[data-reextract]') && (q('[data-reextract]').onclick = () => reextract(l.numero, true));
  q('[data-toggle-rev]') && (q('[data-toggle-rev]').onclick = () => setReviewed(l.numero, !l.reviewed));
  $('assist-info').querySelectorAll('[data-del]').forEach(b => b.onclick = () => delDoc(b.dataset.del));
  $('assist-info').querySelectorAll('[data-retry]').forEach(b => b.onclick = () => retryDoc(b.dataset.retry));
  $('q').disabled = !l.docs.some(d => d.status === 'done'); $('q').placeholder = $('q').disabled ? 'Envie e aguarde a leitura do PDF para perguntar' : 'Pergunte sobre este processo…';
}
async function reextract(numero, ask) {
  if (ask && !confirm('Refazer a extração com todas as partes? As suas edições neste processo serão descartadas.')) return;
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/reextract`, {method: 'POST'}); toast('Extração reiniciada'); refresh(true); } catch (e) { toast(e.message, 'err'); }
}
async function delDoc(id) {
  if (!confirm('Excluir este PDF? O processo será reextraído sem ele.')) return;
  try { await api(`/api/documents/${id}`, {method: 'DELETE'}); toast('PDF excluído'); refresh(true); } catch (e) { toast(e.message, 'err'); }
}
async function retryDoc(id) { try { await api(`/api/documents/${id}/retry`, {method: 'POST'}); toast('PDF reenviado para a fila'); refresh(true); } catch (e) { toast(e.message, 'err'); } }

/* chat */
const SUGGEST = ['Existe valor bloqueado pelo SISBAJUD?', 'Há imóveis penhorados? Matrícula e valor da avaliação', 'Houve tentativa de leilão ou alienação?', 'Há exceção de pré-executividade pendente?', 'Qual é o último andamento?'];
function renderChat() {
  const h = chats[sel] || [];
  $('suggest').innerHTML = h.length ? '' : SUGGEST.map(s => `<button type="button">${esc(s)}</button>`).join('');
  document.querySelectorAll('#suggest button').forEach(b => b.onclick = () => ask(b.textContent));
  $('log').innerHTML = h.length ? h.map((m, i) => m.role === 'u' ? `<div class="bub u">${esc(m.text)}</div>`
    : `<div class="bub a ${m.think ? 'think' : ''} ${m.err ? 'er' : ''}">${esc(m.text)}${m.sources?.length ? `<div class="src">${[...new Set(m.sources)].map(s => `<span>${esc(s)}</span>`).join('')}</div>` : ''}
       ${m.think ? '' : m.err ? `<button class="btn sm" data-again-q="${i}">↻ Tentar de novo</button>` : `<button class="btn sm" data-add="${i}">＋ Adicionar às observações</button>`}</div>`).join('')
    : '<p class="hint">As respostas citam a origem (Evento/ID e página). O histórico vale só nesta sessão do navegador.</p>';
  $('log').scrollTop = 1e9; document.querySelectorAll('[data-add]').forEach(b => b.onclick = () => addToNotes(+b.dataset.add));
  document.querySelectorAll('[data-again-q]').forEach(b => b.onclick = () => { const i = +b.dataset.againQ, q = chats[sel][i].q; chats[sel].splice(i - 1, 2); ask(q); });
}
async function ask(q) {
  q = (q || '').trim(); const l = sel && row(sel); if (!q || !l || $('q').disabled) return; $('q').value = '';
  const num = sel, h = chats[num] = chats[num] || []; h.push({role: 'u', text: q}); h.push({role: 'a', text: 'Consultando o processo…', think: true}); renderChat();
  try { const j = await api('/api/chat', json('POST', {question: q, case_id: caseId, numero: num})); h[h.length - 1] = {role: 'a', text: j.answer, sources: j.sources.map(s => s.rotulo), q}; }
  catch (e) { h[h.length - 1] = {role: 'a', text: e.message, err: true, q}; }
  if (sel === num) renderChat();
}
async function addToNotes(i) {
  const m = chats[sel][i], l = row(sel); if (!l) return;
  if (!editable(l)) return toast('Este processo ainda não tem extração para receber observações.', 'err');
  const novo = `${(l.valores.observacoes || '').trim()}\n\n[Assistente · ${m.q}]\n${m.text}`.trim();
  try { await api(`/api/cases/${caseId}/processes/${enc(sel)}/review`, json('PUT', {observacoes: novo, reviewed: false}));
    l.valores.observacoes = novo; l.reviewed = false; dataKey = JSON.stringify(data); toast('Resposta adicionada às observações (marque como revisado após conferir)', 'ok'); renderGrid(); renderAssistInfo(); }
  catch (e) { toast(e.message, 'err'); }
}
$('chat').onsubmit = e => { e.preventDefault(); ask($('q').value); };

/* ---------- medições: tokens, corte do contexto, tempos ---------- */
const fmtT = x => x == null ? '—' : x < 60 ? `${Math.round(x)} s` : `${Math.floor(x / 60)} min ${Math.round(x % 60)} s`;
const fmtN = n => n == null ? '—' : n >= 1e6 ? `${(n / 1e6).toFixed(2).replace('.', ',')} ${n >= 2e6 ? 'milhões' : 'milhão'}` : n >= 1000 ? `${(n / 1000).toFixed(1).replace('.', ',')} mil` : String(n);
function renderMed() {
  const todos = data.linhas, ls = todos.filter(l => l.medidas || l.docs.length), com = ls.filter(l => l.medidas), cortados = com.filter(l => l.medidas.contexto.cortado);
  const sum = (a, f) => a.reduce((t, x) => t + (f(x) || 0), 0), docs = ls.flatMap(l => l.docs.map(d => d.tempos).filter(Boolean));
  const tin = sum(com, l => l.medidas.tokens?.entrada), tout = sum(com, l => (l.medidas.tokens?.saida || 0) + (l.medidas.tokens?.raciocinio || 0));
  const ocrP = sum(docs, t => t.ocr_paginas), ocrS = sum(docs, t => t.ocr_s);
  const dc = data.documentos, st = x => dc.filter(d => d.status === x).length, naFila = dc.filter(d => d.status === 'queued');
  $('med-sum').innerHTML = `<ul class="med">
    <li>PDFs enviados: <b>${dc.length}</b> · lidos <b>${st('done')}</b> · na fila <b>${st('queued')}</b> · lendo <b>${st('processing')}</b> · com erro <b>${st('error')}</b>. Processos do caso: <b>${todos.length}</b>, com PDF: <b>${ls.length}</b>.</li>
    <li><b>${cortados.length} de ${com.length}</b> processo(s) extraído(s) foram <b>cortados</b>: não couberam no limite enviado à IA.</li>
    <li>Tokens gastos nas extrações: <b>${fmtN(tin)}</b> de entrada e <b>${fmtN(tout)}</b> de saída.</li>
    <li>Tempo de leitura dos PDFs: <b>${fmtT(sum(docs, t => t.total_s))}</b> (OCR: ${ocrP} página(s) em ${fmtT(ocrS)}). Tempo de IA: <b>${fmtT(sum(com, l => l.medidas.extracao_s))}</b>.</li></ul>
    <div class="alert ${cortados.length ? 'warn' : 'ok'}">${!com.length ? 'Ainda não há extrações com medição. Processos extraídos antes desta versão não têm esses dados: use “Refazer extração”.'
      : cortados.length ? `Nos processos cortados a IA não viu todas as páginas. Se isso acontece em <b>muitos</b> processos, vale testar uma busca de páginas melhor (EmbeddingGemma 2); se for em poucos, aumentar <code>CONTEXT_BUDGET_CHARS</code> (quando a cota permitir) já resolve.`
      : 'Nenhum processo foi cortado: a IA viu todas as páginas. Uma busca melhor <b>não mudaria</b> a extração desses processos.'}</div>`;
  $('med-tab').tBodies[0].innerHTML = ls.map(l => { const m = l.medidas, c = m?.contexto, tk = m?.tokens, d = l.docs.map(x => x.tempos).filter(Boolean);
    return `<tr><td><span class="num">${esc(l.numero)}</span></td><td>${c ? c.paginas_total : (l.paginas ?? '—')}</td>
      <td>${c ? `${c.paginas_enviadas} de ${c.paginas_total} ${c.cortado ? '<span class="tag warn">cortado</span>' : '<span class="tag ok">inteiro</span>'}<div class="muted">${fmtN(c.caracteres_enviados)} de ${fmtN(c.caracteres_total)} caracteres</div>` : '—'}</td>
      <td>${tk ? `${fmtN(tk.entrada)} / ${fmtN((tk.saida || 0) + (tk.raciocinio || 0))}` : '—'}</td><td>${m ? fmtT(m.extracao_s) : '—'}</td>
      <td>${d.length ? `${fmtT(sum(d, t => t.total_s))}<div class="muted">OCR: ${sum(d, t => t.ocr_paginas)} pág. em ${fmtT(sum(d, t => t.ocr_s))}</div>` : '—'}</td></tr>`; }).join('') || '<tr><td colspan="6" class="muted">Nenhum processo com PDF enviado ainda.</td></tr>';
}
$('btn-med').onclick = () => { renderMed(); $('dlg-med').showModal(); };

/* ---------- janelas: upload, modelo, PDFs ---------- */
function renderPicked() {
  $('picked').innerHTML = picked.map((f, i) => `<li><span>📄 ${esc(f.name)} <span class="muted">(${(f.size / 1048576).toFixed(1)} MB)</span></span><button type="button" class="x" data-x="${i}" aria-label="Remover">✕</button></li>`).join('');
  document.querySelectorAll('#picked [data-x]').forEach(b => b.onclick = () => { picked.splice(+b.dataset.x, 1); renderPicked(); });
  $('pdfs-btn').disabled = !picked.length;
}
const addFiles = fl => { picked = [...picked, ...[...fl].filter(f => f.name.toLowerCase().endsWith('.pdf'))]; renderPicked(); };
function openUpload(files) { if (files) addFiles(files); $('msg').textContent = ''; $('msg').className = 'msg'; if (!$('dlg-upload').open) $('dlg-upload').showModal(); }
$('btn-upload').onclick = () => openUpload();
$('drop').onclick = () => $('pdfs').click();
$('drop').onkeydown = e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), $('pdfs').click());
$('pdfs').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
['dragenter', 'dragover'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.remove('over'); }));
$('drop').addEventListener('drop', e => addFiles(e.dataTransfer.files));
// arrastar PDFs direto para a planilha abre o envio
const gw = $('grid-wrap');
['dragenter', 'dragover'].forEach(ev => gw.addEventListener(ev, e => { if (!caseId) return; e.preventDefault(); dragging = true; gw.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => gw.addEventListener(ev, e => { e.preventDefault(); dragging = false; gw.classList.remove('over'); }));
gw.addEventListener('drop', e => { if (caseId && e.dataTransfer.files.length) openUpload(e.dataTransfer.files); });
$('pdfs-btn').onclick = async () => {
  if (!picked.length) return; $('pdfs-btn').disabled = true; $('msg').className = 'msg'; let n = 0;
  for (const f of picked) {
    $('msg').textContent = `Enviando ${++n}/${picked.length}: ${f.name}…`;
    const fd = new FormData(); fd.append('file', f); fd.append('case_id', caseId); fd.append('tipo', $('tipo').value); fd.append('sistema', $('sistema').value);
    try { await api('/api/documents', {method: 'POST', body: fd}); }
    catch (e) { $('msg').className = 'msg err'; $('msg').textContent = `${f.name}: ${e.message}`; $('pdfs-btn').disabled = false; return; }
  }
  toast(`${picked.length} PDF(s) enviado(s). A leitura segue em segundo plano.`, 'ok'); picked = []; renderPicked(); $('dlg-upload').close(); refresh(true);
};
function showTemplate(t) {
  tplState = t; $('tpl-del').hidden = !t.ativo; $('tpl-dot').hidden = !t.ativo;
  if (!t.ativo) { $('tpl-report').innerHTML = '<p class="muted">Sem modelo: a tabela e a planilha exportada usam o layout padrão.</p>'; return; }
  const abas = (t.abas || []).map(a => a.preenchida
    ? `<li>✔ <b>${esc(a.aba)}</b>: ${a.reconhecidas.length} coluna(s) reconhecida(s)${a.nao_reconhecidas.length ? `<br><span class="tag warn">Ficam em branco</span> ${esc(a.nao_reconhecidas.join(' · '))}` : ''}</li>`
    : `<li><span class="tag warn">Atenção</span> <b>${esc(a.aba)}</b>: ${esc(a.motivo)}</li>`).join('');
  $('tpl-report').innerHTML = `<ul>${abas}</ul>` + ((t.ausentes || []).length ? `<div class="alert warn">O modelo não tem a(s) aba(s): ${esc(t.ausentes.join(', '))}. Processos desse tipo aparecem só em Relatório.</div>` : '')
    + ((t.ignoradas || []).length ? `<p class="muted">Voltam intactas: ${esc(t.ignoradas.join(', '))}.</p>` : '');
}
$('btn-tpl').onclick = () => $('dlg-tpl').showModal();
$('tpl').onchange = () => { $('tpl-name').textContent = $('tpl').files[0]?.name || 'nenhum arquivo'; };
$('tpl-btn').onclick = async () => {
  const f = $('tpl').files[0]; if (!f) { $('tpl-msg').className = 'msg err'; $('tpl-msg').textContent = 'Escolha a planilha primeiro.'; return; }
  const fd = new FormData(); fd.append('file', f); $('tpl-msg').className = 'msg'; $('tpl-msg').textContent = 'Importando…';
  try { const r = await api(`/api/cases/${caseId}/template`, {method: 'POST', body: fd}); showTemplate({...r, ativo: true});
    $('tpl-msg').textContent = `Modelo importado. ${r.processos_importados} processo(s) lido(s) da planilha.`; refresh(true); }
  catch (e) { $('tpl-msg').className = 'msg err'; $('tpl-msg').textContent = e.message; }
};
$('tpl-del').onclick = async () => { showTemplate(await api(`/api/cases/${caseId}/template`, {method: 'DELETE'})); $('tpl-msg').textContent = 'Modelo removido: será usado o layout padrão.'; refresh(true); };
function openDocs() { renderDocs(); $('dlg-docs').showModal(); }
$('btn-docs').onclick = openDocs;
function renderDocs() {
  if (!data) return;
  $('docs').tBodies[0].innerHTML = data.documentos.map(d => { const [dc, dt] = DST[d.status] || ['', d.status];
    return `<tr><td>${esc(d.filename)}</td><td><span class="tag ${dc}">${dt}</span></td><td>${d.pages_done}/${d.pages_total}${d.pages_ocr ? ` <span class="muted">(${d.pages_ocr} OCR)</span>` : ''}</td>
      <td>${esc(d.error || (d.numero_processo ? 'Processo ' + d.numero_processo : 'nº do processo não identificado'))}</td>
      <td>${['error', 'processing', 'queued'].includes(d.status) ? `<button type="button" class="btn sm" data-retry="${d.id}">Reprocessar</button> ` : ''}<button type="button" class="btn sm danger-ghost" data-del="${d.id}">Excluir</button></td></tr>`; }).join('')
    || '<tr><td colspan="5" class="muted">Nenhum PDF enviado.</td></tr>';
  $('docs').querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { await delDoc(b.dataset.del); renderDocs(); });
  $('docs').querySelectorAll('[data-retry]').forEach(b => b.onclick = async () => { await retryDoc(b.dataset.retry); renderDocs(); });
}

/* ---------- tema claro/escuro ---------- */
function applyTheme(t) { document.documentElement.dataset.theme = t; store.set('tema', t); $('theme').textContent = t === 'dark' ? '☀' : '🌙'; $('theme').title = t === 'dark' ? 'Mudar para tema claro' : 'Mudar para tema escuro'; }
$('theme').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
applyTheme(document.documentElement.dataset.theme || 'light');

/* ---------- inicialização ---------- */
api('/api/tipos').then(t => { schema = t; for (const [k, v] of Object.entries(t.tipos)) $('tipo').add(new Option(v, k)); for (const [k, v] of Object.entries(t.sistemas)) $('sistema').add(new Option(v, k)); });
api('/api/health').then(h => { $('llm').textContent = h.llm ? `IA ativa · ${h.llm}` : 'IA desativada (sem chave)'; }).catch(() => {});
loadCases();
