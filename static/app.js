const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let caseId = null, selDoc = null, timer = null;
const api = async (url, opt) => { const r = await fetch(url, opt); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || 'Erro'); return j; };
const json = (m, b) => ({ method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b) });

async function loadCases() {
  const cs = await api('/api/cases');
  $('cases').innerHTML = cs.map(c => `<li tabindex="0" data-id="${c.id}"><b>${esc(c.nome)}</b></li>`).join('') || '<li>Nenhum caso. Crie o primeiro acima.</li>';
  document.querySelectorAll('#cases li[data-id]').forEach(li => { li.onclick = () => openCase(li.dataset.id); li.onkeydown = e => e.key === 'Enter' && openCase(li.dataset.id); });
}
async function openCase(id) {
  caseId = id; selDoc = null;
  $('view-cases').classList.add('hidden'); $('view-case').classList.remove('hidden');
  $('export').href = `/api/cases/${id}/spreadsheet`;
  await refreshCase(); clearInterval(timer); timer = setInterval(refreshCase, 2500);
}
function home() { clearInterval(timer); caseId = null; $('view-case').classList.add('hidden'); $('view-cases').classList.remove('hidden'); $('crumb').textContent = ''; loadCases(); }
const stTag = d => !d ? '<span class="tag">sem PDF</span>' : d.status === 'done' ? '<span class="tag ok">concluído</span>'
  : d.status === 'error' ? `<span class="tag err" title="${esc(d.error)}">erro</span>`
  : `<span class="tag warn">${d.status}</span><div class="bar"><i style="width:${d.pages_total ? 100 * d.pages_done / d.pages_total : 0}%"></i></div>`;
async function refreshCase() {
  if (!caseId) return;
  const c = await api(`/api/cases/${caseId}`);
  $('case-title').textContent = c.nome; $('crumb').innerHTML = ' &rsaquo; ' + esc(c.nome);
  const docs = Object.fromEntries(c.documentos.map(d => [d.numero_processo, d]));
  const nums = [...new Set([...c.processos.map(p => p.numero), ...c.documentos.map(d => d.numero_processo).filter(Boolean)])];
  const proc = Object.fromEntries(c.processos.map(p => [p.numero, p.mcfly || {}]));
  $('procs').tBodies[0].innerHTML = nums.map(n => {
    const d = docs[n];
    return `<tr class="${d && d.id === selDoc ? 'sel' : ''}"><td>${esc(n)}</td><td>${esc((proc[n] || {}).classe || (d && d.classe))}</td><td>${esc(d && d.sheet || '')}</td><td>${stTag(d)}</td>
      <td>${d && d.status === 'done' ? (d.reviewed ? '<span class="tag ok">revisado</span>' : '<span class="tag warn">pendente</span>') : ''}</td>
      <td>${d && d.status === 'done' ? `<button class="btn" data-open="${d.id}">Revisar / perguntar</button>` : ''}</td></tr>`;
  }).join('') || '<tr><td colspan="6">Importe o Mcfly e envie os PDFs.</td></tr>';
  document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => selectDoc(b.dataset.open));
}
async function selectDoc(id) {
  selDoc = id; const d = await api(`/api/documents/${id}`); const r = d.result; const f = r.fields;
  $('review').classList.remove('hidden');
  $('rev-title').textContent = `${d.numero_processo || d.filename} (modelo: ${r.prompt})`;
  $('rev-form').innerHTML = Object.entries(f).filter(([k]) => !['observacoes', 'digitalizado'].includes(k)).map(([k, v]) =>
    `<div class="field"><label for="f-${k}">${esc(k.replace(/_/g, ' '))}</label><input type="text" id="f-${k}" data-k="${k}" value="${esc(v)}">
     <span class="cit">${esc((r.citacoes || {})[k] || '')}</span></div>`).join('') +
    `<div class="field"><label for="f-obs">Observações relevantes (relatório)</label><textarea id="f-obs">${esc(f.observacoes)}</textarea></div>` +
    (r.revisar?.length ? `<p class="tag warn">Campos fora da lista permitida, revisar: ${esc(r.revisar.join(', '))}</p>` : '') +
    (r.bens?.length ? `<p class="hint">${r.bens.length} bem(ns) extraído(s) para a aba Outros Bens.</p>` : '') +
    `<button class="btn primary">Salvar e marcar como revisado</button> <span id="rev-msg" role="status"></span>`;
  $('review').scrollIntoView({behavior: 'smooth'}); refreshCase();
}
$('rev-form').onsubmit = async e => {
  e.preventDefault();
  const fields = {}; document.querySelectorAll('#rev-form [data-k]').forEach(i => fields[i.dataset.k] = i.value.trim() || null);
  try { await api(`/api/documents/${selDoc}/review`, {...json('PUT', {fields, observacoes: $('f-obs').value, reviewed: true})}); $('rev-msg').textContent = 'Salvo.'; refreshCase(); }
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
  for (const f of files) { const fd = new FormData(); fd.append('file', f); fd.append('case_id', caseId);
    try { await api('/api/documents', {method: 'POST', body: fd}); } catch (e) { $('msg').textContent = `${f.name}: ${e.message}`; return; } }
  $('msg').textContent = `${files.length} arquivo(s) enviado(s). Processamento em segundo plano.`; refreshCase();
};
const add = (cls, html) => { const p = document.createElement('p'); p.className = 'msg ' + cls; p.innerHTML = html; $('log').appendChild(p); $('log').scrollTop = 1e9; };
$('chat').onsubmit = async e => {
  e.preventDefault(); const q = $('q').value.trim(); if (!q) return;
  if (!selDoc) return add('', 'Selecione um processo na tabela (botão “Revisar / perguntar”).');
  $('q').value = ''; add('u', esc(q));
  try { const j = await api('/api/chat', json('POST', {question: q, doc_id: selDoc}));
    add('', esc(j.answer) + (j.sources.length ? `<div class="src">Fontes: ${[...new Set(j.sources.map(s => s.rotulo))].map(esc).join(' · ')}</div>` : '')); }
  catch (err) { add('', esc(err.message)); }
};
api('/api/health').then(h => $('llm').textContent = h.llm ? 'IA ativa: ' + h.llm : 'IA desativada (sem chave)').catch(() => {});
loadCases();
