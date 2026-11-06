const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api = async (url, opt) => {
  const r = await fetch(url, opt); let j = {};
  try { j = await r.json(); } catch (_) { /* resposta sem JSON */ }
  if (!r.ok) { const d = j.detail; throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Falha HTTP ${r.status}`); }
  return j;
};
const json = (m, b) => ({ method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b) });
const enc = encodeURIComponent;

let caseId = null, selProc = null, tab = 'rev', timer = null, schema = {campos: {}}, tplState = {ativo: false}, picked = [], last = null;
const chats = {};  // histórico do assistente por processo (só nesta sessão do navegador)

/* ---------- casos ---------- */
async function loadCases() {
  const cs = await api('/api/cases');
  $('cases').innerHTML = cs.map(c => `<li tabindex="0" data-id="${c.id}">${esc(c.nome)}</li>`).join('') || '<li style="cursor:default">Nenhum caso ainda. Crie o primeiro acima.</li>';
  document.querySelectorAll('#cases li[data-id]').forEach(li => { li.onclick = () => openCase(li.dataset.id); li.onkeydown = e => e.key === 'Enter' && openCase(li.dataset.id); });
}
async function openCase(id) {
  caseId = id; selProc = null; picked = []; renderPicked(); closeSide();
  $('view-cases').classList.add('hidden'); $('view-case').classList.remove('hidden');
  $('export').href = `/api/cases/${id}/spreadsheet`;
  tplState = await api(`/api/cases/${id}/template`).catch(() => ({ativo: false})); showTemplate(tplState);
  await refreshCase(); clearInterval(timer); timer = setInterval(refreshCase, 2500);
}
function home(e) { e && e.preventDefault(); clearInterval(timer); caseId = null; $('view-case').classList.add('hidden'); $('view-cases').classList.remove('hidden'); loadCases(); }

/* ---------- planilha-modelo ---------- */
function showTemplate(t) {
  tplState = t; $('tpl-del').hidden = !t.ativo; $('tpl-chip').hidden = !t.ativo;
  $('tpl-chip').textContent = 'importada'; $('tpl-chip').className = 'chip tag ok';
  $('tpl-details').hidden = !t.ativo;
  if (!t.ativo) { $('tpl-report').innerHTML = ''; return; }
  const abas = (t.abas || []).map(a => a.preenchida
    ? `<li>✔ <b>${esc(a.aba)}</b>: ${a.reconhecidas.length} coluna(s) reconhecida(s)${a.nao_reconhecidas.length ? `<br><span class="tag warn">Ficam em branco</span> ${esc(a.nao_reconhecidas.join(' · '))}` : ''}</li>`
    : `<li><span class="tag warn">Atenção</span> <b>${esc(a.aba)}</b>: ${esc(a.motivo)}</li>`).join('');
  $('tpl-report').innerHTML = `<ul>${abas}</ul>`
    + ((t.ausentes || []).length ? `<div class="alert warn">O modelo não tem a(s) aba(s): ${esc(t.ausentes.join(', '))}. Processos desse tipo aparecem só em Relatório.</div>` : '')
    + ((t.ignoradas || []).length ? `<p class="hint">Voltam intactas: ${esc(t.ignoradas.join(', '))}.</p>` : '');
  renderSteps();
}
$('tpl').onchange = () => { $('tpl-name').textContent = $('tpl').files[0]?.name || 'nenhum arquivo'; };
$('tpl-btn').onclick = async () => {
  const f = $('tpl').files[0]; if (!f) { $('tpl-msg').textContent = 'Escolha a planilha primeiro.'; return; }
  const fd = new FormData(); fd.append('file', f); $('tpl-msg').className = 'msg'; $('tpl-msg').textContent = 'Importando…';
  try {
    const r = await api(`/api/cases/${caseId}/template`, {method: 'POST', body: fd});
    showTemplate({...r, ativo: true}); $('tpl-details').open = true;
    $('tpl-msg').textContent = `Modelo importado. ${r.processos_importados} processo(s) lido(s) da planilha.`; refreshCase();
  } catch (e) { $('tpl-msg').className = 'msg err'; $('tpl-msg').textContent = e.message; }
};
$('tpl-del').onclick = async () => { showTemplate(await api(`/api/cases/${caseId}/template`, {method: 'DELETE'})); $('tpl-msg').textContent = 'Modelo removido: será usado o layout padrão.'; };

/* ---------- envio de PDFs ---------- */
function renderPicked() {
  $('picked').innerHTML = picked.map(f => `• ${esc(f.name)} <span class="cit">(${(f.size / 1048576).toFixed(1)} MB)</span>`).join('<br>');
  $('pdfs-btn').disabled = !picked.length;
}
const addFiles = fl => { picked = [...picked, ...[...fl].filter(f => f.name.toLowerCase().endsWith('.pdf'))]; renderPicked(); };
$('drop').onclick = () => $('pdfs').click();
$('drop').onkeydown = e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), $('pdfs').click());
$('pdfs').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
['dragenter', 'dragover'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.remove('over'); }));
$('drop').addEventListener('drop', e => addFiles(e.dataTransfer.files));
$('pdfs-btn').onclick = async () => {
  if (!picked.length) return; $('pdfs-btn').disabled = true; $('msg').className = 'msg'; let n = 0;
  for (const f of picked) {
    $('msg').textContent = `Enviando ${++n}/${picked.length}: ${f.name}…`;
    const fd = new FormData(); fd.append('file', f); fd.append('case_id', caseId); fd.append('tipo', $('tipo').value); fd.append('sistema', $('sistema').value);
    try { await api('/api/documents', {method: 'POST', body: fd}); }
    catch (e) { $('msg').className = 'msg err'; $('msg').textContent = `${f.name}: ${e.message}`; $('pdfs-btn').disabled = false; return; }
  }
  $('msg').textContent = `${picked.length} arquivo(s) enviado(s). O processamento segue em segundo plano.`; picked = []; renderPicked(); refreshCase();
};

/* ---------- tabela de processos e etapas ---------- */
const ESTADO = {pronto: ['ok', 'pronto'], extraindo: ['warn', 'extraindo…'], aguardando: ['warn', 'aguardando PDFs'],
  desatualizado: ['warn', 'novas partes'], erro: ['err', 'erro'], sem_pdf: ['', 'sem PDF']};
const DST = {queued: ['warn', 'na fila'], processing: ['warn', 'processando'], done: ['ok', 'lido'], error: ['err', 'erro']};
const MODELO = {execucoes_fiscais: 'Execução fiscal', outras_acoes: 'Outras ações', recursos: 'Recursos'};
const isReady = p => ['pronto', 'desatualizado'].includes(p.estado);

function renderSteps() {
  if (!last) return;
  const procs = last.processos, ready = procs.filter(isReady), rev = ready.filter(p => p.reviewed).length;
  const set = (n, done, now, text) => { const li = $('st-' + n); li.className = done ? 'done' : now ? 'now' : ''; $('st-' + n + 't').textContent = text; };
  set(1, tplState.ativo, !tplState.ativo && !last.documentos.length, tplState.ativo ? 'importada' : 'opcional · layout padrão');
  set(2, last.documentos.length > 0, !!tplState.ativo && !last.documentos.length, last.documentos.length ? `${last.documentos.length} PDF(s)` : 'aguardando envio');
  set(3, ready.length > 0 && rev === ready.length, ready.length > rev, ready.length ? `${rev}/${ready.length} revisados` : 'sem processos prontos');
  set(4, false, ready.length > 0 && rev === ready.length, procs.length ? 'baixe a planilha' : '—');
}
async function refreshCase() {
  if (!caseId) return;
  const c = last = await api(`/api/cases/${caseId}`);
  $('case-title').textContent = c.nome; $('crumb').textContent = c.nome;
  const docs = Object.fromEntries(c.documentos.map(d => [d.id, d]));
  $('count').textContent = `${c.processos.length} processo(s)`;
  $('procs').tBodies[0].innerHTML = c.processos.map(p => {
    const ds = p.documentos.map(i => docs[i]).filter(Boolean);
    const busy = ds.filter(d => d.status === 'queued' || d.status === 'processing');
    const pct = ds.reduce((a, d) => a + d.pages_done, 0) / Math.max(1, ds.reduce((a, d) => a + d.pages_total, 0));
    const [cls, txt] = ESTADO[p.estado];
    return `<tr class="${isReady(p) ? 'ready' : ''} ${p.numero === selProc ? 'sel' : ''}" data-n="${esc(p.numero)}">
      <td><span class="mono">${esc(p.numero)}</span></td><td>${esc((p.mcfly || {}).classe || p.classe || '')}</td><td>${esc(MODELO[p.modelo] || p.modelo || '')}</td>
      <td>${ds.length ? ds.length + ' PDF(s)' : ''}${busy.length ? `<div class="bar-p"><i style="width:${Math.round(100 * pct)}%"></i></div>` : ''}</td>
      <td><span class="tag ${cls}">${txt}</span> ${p.estado === 'pronto' ? (p.reviewed ? '<span class="tag ok">revisado</span>' : '<span class="tag blue">revisar</span>') : ''}
        ${p.error ? `<div class="err-msg">${esc(p.error)}</div>` : ''}
        ${p.estado === 'erro' && ds.length ? `<button class="btn sm" data-again="${esc(p.numero)}">Tentar novamente</button>` : ''}</td></tr>`;
  }).join('') || '<tr><td colspan="5" class="hint">Nenhum processo ainda. Importe a planilha-modelo e envie os PDFs.</td></tr>';
  document.querySelectorAll('#procs tr.ready').forEach(tr => tr.onclick = () => selectProc(tr.dataset.n));
  document.querySelectorAll('[data-again]').forEach(b => b.onclick = async e => { e.stopPropagation(); await api(`/api/cases/${caseId}/processes/${enc(b.dataset.again)}/reextract`, {method: 'POST'}); refreshCase(); });
  const orphans = c.documentos.filter(d => !d.numero_processo && !['queued', 'processing'].includes(d.status));
  $('orphans').textContent = orphans.length ? `${orphans.length} PDF(s) sem nº de processo identificado: ${orphans.map(d => d.filename).join(', ')}. Renomeie com o nº CNJ e reenvie.` : '';
  $('docs-count').textContent = `(${c.documentos.length})`;
  $('docs').tBodies[0].innerHTML = c.documentos.map(d => {
    const [dc, dt] = DST[d.status] || ['', d.status];
    return `<tr><td>${esc(d.filename)}</td><td><span class="tag ${dc}">${dt}</span></td>
      <td>${d.pages_done}/${d.pages_total}${d.pages_ocr ? ` <span class="cit">(${d.pages_ocr} por OCR)</span>` : ''}</td>
      <td>${esc(d.error || (d.numero_processo ? 'Processo ' + d.numero_processo : 'nº do processo não identificado'))}</td>
      <td>${['error', 'processing', 'queued'].includes(d.status) ? `<button class="btn sm" data-retry="${d.id}">Reprocessar</button> ` : ''}<button class="btn sm danger-ghost" data-del="${d.id}">Excluir</button></td></tr>`;
  }).join('') || '<tr><td colspan="5" class="hint">Nenhum PDF enviado.</td></tr>';
  if (c.documentos.some(d => ['error', 'queued', 'processing'].includes(d.status))) $('docs-card').open = true;
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { if (confirm('Excluir este PDF? O processo será reextraído sem ele.')) { await api(`/api/documents/${b.dataset.del}`, {method: 'DELETE'}); refreshCase(); } });
  document.querySelectorAll('[data-retry]').forEach(b => b.onclick = async () => { await api(`/api/documents/${b.dataset.retry}/retry`, {method: 'POST'}); refreshCase(); });
  renderSteps();
}

/* ---------- painel lateral: revisão + assistente ---------- */
function closeSide() { selProc = null; $('side-body').hidden = true; $('side-empty').hidden = false; document.querySelectorAll('#procs tr.sel').forEach(t => t.classList.remove('sel')); }
function setTab(t) {
  tab = t; $('tab-rev').className = t === 'rev' ? 'on' : ''; $('tab-chat').className = t === 'chat' ? 'on' : '';
  $('tab-rev').setAttribute('aria-selected', t === 'rev'); $('tab-chat').setAttribute('aria-selected', t === 'chat');
  $('pane-rev').hidden = t !== 'rev'; $('pane-chat').hidden = t !== 'chat'; if (t === 'chat') $('q').focus();
}
const SUGGEST = ['Existe valor bloqueado pelo SISBAJUD?', 'Há imóveis penhorados? Com matrícula e valor da avaliação', 'Houve tentativa de leilão ou alienação?',
  'Há exceção de pré-executividade pendente?', 'Qual é o último andamento do processo?'];

async function selectProc(numero) {
  selProc = numero; document.querySelectorAll('#procs tbody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.n === numero));
  const p = await api(`/api/cases/${caseId}/processes/${enc(numero)}`); const r = p.result; const f = r.fields;
  $('side-empty').hidden = true; $('side-body').hidden = false; $('side-title').textContent = numero;
  $('side-chips').innerHTML = [`<span class="tag ${p.reviewed ? 'ok' : 'blue'}">${p.reviewed ? 'revisado' : 'a revisar'}</span>`,
    `<span class="chip">${esc(MODELO[r.prompt] || r.prompt)}</span>`, `<span class="chip">${esc(r.sistema || 'sistema não detectado')}</span>`,
    `<span class="chip">${(r.documentos || []).length} PDF(s) · ${r.paginas} págs.</span>`, r.provedor ? `<span class="chip">IA: ${esc(r.provedor)}</span>` : ''].join('');
  const meta = (schema.campos[r.sheet]) || {};
  const field = ([k, v]) => {
    const m = meta[k] || {titulo: k.replace(/_/g, ' '), opcoes: null};
    const input = m.opcoes
      ? `<select id="f-${k}" data-k="${k}"><option value=""></option>${m.opcoes.map(o => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
      : `<input type="text" id="f-${k}" data-k="${k}" value="${esc(v)}">`;
    return `<div class="${(m.titulo.length > 38 || String(v ?? '').length > 40) ? 'full' : ''}"><label for="f-${k}">${esc(m.titulo)}</label>${input}<span class="cit">${esc((r.citacoes || {})[k] || '')}</span></div>`;
  };
  $('rev-form').innerHTML =
    (r.citacoes_invalidas?.length ? `<div class="alert err"><b>Citações que não existem no PDF</b> (conferir): ${esc(r.citacoes_invalidas.join('; '))}</div>` : '') +
    (r.revisar?.length ? `<div class="alert warn">Campos fora da lista permitida (ficaram vazios): ${esc(r.revisar.join(', '))}</div>` : '') +
    (r.modo === 'sem_llm' ? '<div class="alert warn">A IA não estava configurada: esta extração está vazia.</div>' : '') +
    `<div class="fields">${Object.entries(f).filter(([k]) => !['observacoes', 'digitalizado'].includes(k)).map(field).join('')}
      <div class="full"><label for="f-obs">Observações relevantes (relatório)</label><textarea id="f-obs">${esc(f.observacoes)}</textarea></div></div>` +
    (r.bens?.length ? `<p class="hint">${r.bens.length} bem(ns) extraído(s) para a aba Outros Bens.</p>` : '') +
    `<div class="actions"><button class="btn primary">Salvar e marcar como revisado</button>
      <button type="button" class="btn" id="reext">Refazer extração</button><span class="msg" id="rev-msg" role="status"></span></div>`;
  $('reext').onclick = async () => { if (confirm('Refazer a extração com todas as partes? Suas edições serão descartadas.')) { await api(`/api/cases/${caseId}/processes/${enc(numero)}/reextract`, {method: 'POST'}); closeSide(); refreshCase(); } };
  $('suggest').innerHTML = SUGGEST.map(s => `<button type="button">${esc(s)}</button>`).join('');
  document.querySelectorAll('#suggest button').forEach(b => b.onclick = () => ask(b.textContent));
  renderLog(); setTab(tab);
}
$('rev-form').onsubmit = async e => {
  e.preventDefault(); const fields = {};
  document.querySelectorAll('#rev-form [data-k]').forEach(i => fields[i.dataset.k] = i.value.trim() || null);
  try { await api(`/api/cases/${caseId}/processes/${enc(selProc)}/review`, json('PUT', {fields, observacoes: $('f-obs').value, reviewed: true}));
    $('rev-msg').className = 'msg'; $('rev-msg').textContent = '✔ Salvo e marcado como revisado.'; refreshCase(); }
  catch (err) { $('rev-msg').className = 'msg err'; $('rev-msg').textContent = err.message; }
};
$('tab-rev').onclick = () => setTab('rev'); $('tab-chat').onclick = () => setTab('chat'); $('side-close').onclick = closeSide;

/* assistente */
function renderLog() {
  const h = chats[selProc] || [];
  $('log').innerHTML = h.length ? h.map((m, i) => m.role === 'u' ? `<div class="bub u">${esc(m.text)}</div>`
    : `<div class="bub a ${m.think ? 'think' : ''}">${esc(m.text)}${m.sources?.length ? `<div class="src">${[...new Set(m.sources)].map(s => `<span>${esc(s)}</span>`).join('')}</div>` : ''}
       ${m.think || m.err ? '' : `<div class="act"><button class="btn sm" data-add="${i}">Adicionar às observações</button></div>`}</div>`).join('')
    : '<p class="hint">Faça uma pergunta sobre este processo ou use uma sugestão acima. As respostas citam a origem (Evento/ID e página). O histórico vale só nesta sessão.</p>';
  $('log').scrollTop = 1e9;
  document.querySelectorAll('[data-add]').forEach(b => b.onclick = () => addToNotes(+b.dataset.add));
}
async function ask(q) {
  q = (q || '').trim(); if (!q || !selProc) return; setTab('chat'); $('q').value = '';
  const h = chats[selProc] = chats[selProc] || []; const num = selProc;
  h.push({role: 'u', text: q}); h.push({role: 'a', text: 'Consultando o processo…', think: true}); renderLog();
  try { const j = await api('/api/chat', json('POST', {question: q, case_id: caseId, numero: num}));
    h[h.length - 1] = {role: 'a', text: j.answer, sources: j.sources.map(s => s.rotulo), q}; }
  catch (e) { h[h.length - 1] = {role: 'a', text: e.message, err: true}; }
  if (selProc === num) renderLog();
}
async function addToNotes(i) {
  const m = chats[selProc][i]; const area = $('f-obs'); if (!area) return;
  area.value = `${area.value.trim()}\n\n[Assistente · ${m.q}]\n${m.text}`.trim();
  try { await api(`/api/cases/${caseId}/processes/${enc(selProc)}/review`, json('PUT', {observacoes: area.value, reviewed: false}));
    $('rev-msg').className = 'msg'; $('rev-msg').textContent = 'Resposta adicionada às observações (confira e marque como revisado).'; setTab('rev'); refreshCase(); }
  catch (e) { $('rev-msg').className = 'msg err'; $('rev-msg').textContent = e.message; }
}
$('chat').onsubmit = e => { e.preventDefault(); ask($('q').value); };

/* ---------- inicialização ---------- */
$('home').onclick = home; $('crumb-home').onclick = home;
$('new-case').onsubmit = async e => { e.preventDefault(); const c = await api('/api/cases', json('POST', {nome: $('case-name').value})); $('case-name').value = ''; openCase(c.id); };
api('/api/tipos').then(t => { schema = t; for (const [k, v] of Object.entries(t.tipos)) $('tipo').add(new Option(v, k)); for (const [k, v] of Object.entries(t.sistemas)) $('sistema').add(new Option(v, k)); });
api('/api/health').then(h => { $('llm').textContent = h.llm ? `IA ativa · ${h.llm}` : 'IA desativada (sem chave)'; }).catch(() => {});
loadCases();
