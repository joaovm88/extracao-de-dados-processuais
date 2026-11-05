const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let caseId = null, selProc = null, timer = null;
const api = async (url, opt) => {
  const r = await fetch(url, opt); let j = {};
  try { j = await r.json(); } catch (_) { /* resposta sem JSON */ }
  if (!r.ok) {
    const d = j.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Falha HTTP ${r.status}`);
  }
  return j;
};
const json = (m, b) => ({ method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b) });

async function loadCases() {
  const cs = await api('/api/cases');
  $('cases').innerHTML = cs.map(c => `<li tabindex="0" data-id="${c.id}"><b>${esc(c.nome)}</b></li>`).join('') || '<li>Nenhum caso. Crie o primeiro acima.</li>';
  document.querySelectorAll('#cases li[data-id]').forEach(li => { li.onclick = () => openCase(li.dataset.id); li.onkeydown = e => e.key === 'Enter' && openCase(li.dataset.id); });
}
async function openCase(id) {
  caseId = id; selProc = null;
  $('view-cases').classList.add('hidden'); $('view-case').classList.remove('hidden');
  $('export').href = `/api/cases/${id}/spreadsheet`;
  await refreshCase(); clearInterval(timer); timer = setInterval(refreshCase, 2500);
}
function home() { clearInterval(timer); caseId = null; $('view-case').classList.add('hidden'); $('view-cases').classList.remove('hidden'); $('crumb').textContent = ''; loadCases(); }
const stTag = d => !d ? '<span class="tag">sem PDF</span>' : d.status === 'done' ? '<span class="tag ok">concluído</span>'
  : d.status === 'error' ? `<span class="tag err" title="${esc(d.error)}">erro</span>`
  : `<span class="tag warn">${d.status}</span><div class="bar"><i style="width:${d.pages_total ? 100 * d.pages_done / d.pages_total : 0}%"></i></div>`;
const ESTADO = {pronto: ['ok', 'pronto'], extraindo: ['warn', 'extraindo…'], aguardando: ['warn', 'aguardando PDFs'],
  desatualizado: ['warn', 'novas partes: reextrair'], erro: ['err', 'erro'], sem_pdf: ['', 'sem PDF']};
let lastCase = null;
async function refreshCase() {
  if (!caseId) return;
  const c = lastCase = await api(`/api/cases/${caseId}`);
  $('case-title').textContent = c.nome; $('crumb').innerHTML = ' &rsaquo; ' + esc(c.nome);
  const docs = Object.fromEntries(c.documentos.map(d => [d.id, d]));
  const rows = c.processos.map(p => {
    const ds = p.documentos.map(i => docs[i]);
    const andamento = ds.filter(d => d.status === 'queued' || d.status === 'processing');
    const pct = ds.reduce((a, d) => a + d.pages_done, 0) / Math.max(1, ds.reduce((a, d) => a + d.pages_total, 0));
    const [cls, txt] = ESTADO[p.estado];
    return `<tr class="${p.numero === selProc ? 'sel' : ''}"><td>${esc(p.numero)}</td><td>${esc((p.mcfly || {}).classe || p.classe)}</td>
      <td>${p.modelo ? esc(p.modelo) : ''}</td>
      <td>${ds.length ? ds.length + ' PDF(s)' : ''}${andamento.length ? `<div class="bar"><i style="width:${100 * pct}%"></i></div>` : ''}</td>
      <td><span class="tag ${cls}">${txt}</span> ${p.estado === 'pronto' ? (p.reviewed ? '<span class="tag ok">revisado</span>' : '<span class="tag warn">revisar</span>') : ''}</td>
      <td>${['pronto', 'desatualizado'].includes(p.estado) ? `<button class="btn" data-open="${esc(p.numero)}">Revisar / perguntar</button>` : ''}</td></tr>`;
  });
  const avulsos = c.documentos.filter(d => !d.numero_processo && d.status !== 'queued' && d.status !== 'processing');
  $('procs').tBodies[0].innerHTML = rows.join('') || '<tr><td colspan="6">Importe o Mcfly e envie os PDFs.</td></tr>';
  $('orphans').textContent = avulsos.length ? `${avulsos.length} PDF(s) sem nº de processo identificado: ${avulsos.map(d => d.filename).join(', ')}` : '';
  document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => selectProc(b.dataset.open));
}
async function selectProc(numero) {
  selProc = numero; const p = await api(`/api/cases/${caseId}/processes/${encodeURIComponent(numero)}`);
  const r = p.result; const f = r.fields;
  $('review').classList.remove('hidden');
  $('rev-title').textContent = `${numero} (modelo: ${r.prompt} · ${r.sistema || 'sistema não detectado'} · ${(r.documentos || []).length} PDF(s), ${r.paginas} págs.)`;
  $('rev-form').innerHTML = Object.entries(f).filter(([k]) => !['observacoes', 'digitalizado'].includes(k)).map(([k, v]) =>
    `<div class="field"><label for="f-${k}">${esc(k.replace(/_/g, ' '))}</label><input type="text" id="f-${k}" data-k="${k}" value="${esc(v)}">
     <span class="cit">${esc((r.citacoes || {})[k] || '')}</span></div>`).join('') +
    `<div class="field"><label for="f-obs">Observações relevantes (relatório)</label><textarea id="f-obs">${esc(f.observacoes)}</textarea></div>` +
    (r.citacoes_invalidas?.length ? `<p class="tag err">Citações que não existem no PDF (conferir): ${esc(r.citacoes_invalidas.join('; '))}</p>` : '') +
    (r.revisar?.length ? `<p class="tag warn">Campos fora da lista permitida, revisar: ${esc(r.revisar.join(', '))}</p>` : '') +
    (r.bens?.length ? `<p class="hint">${r.bens.length} bem(ns) extraído(s) para a aba Outros Bens.</p>` : '') +
    `<button class="btn primary">Salvar e marcar como revisado</button> <button type="button" class="btn" id="reext">Refazer extração (descarta edições)</button> <span id="rev-msg" role="status"></span>`;
  $('reext').onclick = async () => { if (confirm('Refazer a extração com todas as partes? Suas edições serão descartadas.')) { await api(`/api/cases/${caseId}/processes/${encodeURIComponent(numero)}/reextract`, {method: 'POST'}); $('review').classList.add('hidden'); refreshCase(); } };
  $('review').scrollIntoView({behavior: 'smooth'}); refreshCase();
}
$('rev-form').onsubmit = async e => {
  e.preventDefault();
  const fields = {}; document.querySelectorAll('#rev-form [data-k]').forEach(i => fields[i.dataset.k] = i.value.trim() || null);
  try { await api(`/api/cases/${caseId}/processes/${encodeURIComponent(selProc)}/review`, json('PUT', {fields, observacoes: $('f-obs').value, reviewed: true})); $('rev-msg').textContent = 'Salvo.'; refreshCase(); }
  catch (err) { $('rev-msg').textContent = err.message; }
};
$('home').onclick = e => { e.preventDefault(); home(); };
$('new-case').onsubmit = async e => { e.preventDefault(); const c = await api('/api/cases', json('POST', {nome: $('case-name').value})); $('case-name').value = ''; openCase(c.id); };
$('mcfly-btn').onclick = async () => {
  const f = $('mcfly').files[0]; if (!f) return; const fd = new FormData(); fd.append('file', f);
  try { const r = await api(`/api/cases/${caseId}/mcfly`, {method: 'POST', body: fd}); $('msg').textContent = `${r.importados} processos importados.`; refreshCase(); } catch (e) { $('msg').textContent = e.message; }
};
$('pdfs-btn').onclick = async () => {
  const files = [...$('pdfs').files]; if (!files.length) return; $('msg').textContent = 'Enviando…';
  for (const f of files) { const fd = new FormData(); fd.append('file', f); fd.append('case_id', caseId); fd.append('tipo', $('tipo').value); fd.append('sistema', $('sistema').value);
    try { await api('/api/documents', {method: 'POST', body: fd}); } catch (e) { $('msg').textContent = `${f.name}: ${e.message}`; return; } }
  $('msg').textContent = `${files.length} arquivo(s) enviado(s). Processamento em segundo plano.`; refreshCase();
};
const add = (cls, html) => { const p = document.createElement('p'); p.className = 'msg ' + cls; p.innerHTML = html; $('log').appendChild(p); $('log').scrollTop = 1e9; };
$('chat').onsubmit = async e => {
  e.preventDefault(); const q = $('q').value.trim(); if (!q) return;
  if (!selProc) return add('', 'Selecione um processo na tabela (botão “Revisar / perguntar”).');
  $('q').value = ''; add('u', esc(q));
  try { const j = await api('/api/chat', json('POST', {question: q, case_id: caseId, numero: selProc}));
    add('', esc(j.answer) + (j.sources.length ? `<div class="src">Fontes: ${[...new Set(j.sources.map(s => s.rotulo))].map(esc).join(' · ')}</div>` : '')); }
  catch (err) { add('', esc(err.message)); }
};
api('/api/tipos').then(t => { for (const [k, v] of Object.entries(t.tipos)) $('tipo').add(new Option(v, k)); for (const [k, v] of Object.entries(t.sistemas)) $('sistema').add(new Option(v, k)); });
api('/api/health').then(h => $('llm').textContent = h.llm ? 'IA ativa: ' + h.llm : 'IA desativada (sem chave)').catch(() => {});
loadCases();
