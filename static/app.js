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
const defW = k => ({__first: 300, observacoes: 320, corresponsaveis: 380, inscricoes: 220, somatorio: 200, rj_emp: 300, rj_vara: 260, rj_datas: 240, rj_and: 340, rj_rel: 200}[k] ?? 150);
const RJ_COLS = [{key: 'rj_emp', titulo: 'Empresas recuperandas', fonte: 'rj'}, {key: 'rj_vara', titulo: 'Vara e administrador judicial', fonte: 'rj'}, {key: 'rj_datas', titulo: 'Datas', fonte: 'rj'}, {key: 'rj_and', titulo: 'Andamento', fonte: 'rj'}, {key: 'rj_rel', titulo: 'Relatório (Word / PDF)', fonte: 'rj'}];
const wOf = (a, k) => tam.larg[a]?.[k] ?? defW(k);
function loadTam() { try { const t = JSON.parse(store.get('tam:' + caseId) || '{}'); tam = {larg: t.larg || {}, alt: t.alt || {}, dens: t.dens || 'normal'}; } catch (_) { tam = {larg: {}, alt: {}, dens: 'normal'}; } }
const saveTam = () => store.set('tam:' + caseId, JSON.stringify(tam));
const LONG = ['observacoes', 'corresponsaveis'];  // colunas de texto longo: abrem numa janela para ler/editar tudo
const chats = {};  // histórico do assistente por processo (só nesta sessão do navegador)
const ESTADO = {pronto: ['ok', 'pronto'], extraindo: ['warn', 'extraindo…'], aguardando: ['warn', 'aguardando PDFs'], desatualizado: ['warn', 'novas partes'], erro: ['err', 'erro'], sem_pdf: ['', 'sem PDF'], manual: ['blue', 'feito manualmente']};
const DST = {queued: ['warn', 'na fila'], processing: ['warn', 'processando'], done: ['ok', 'lido'], error: ['err', 'erro']};
const MODELO = {execucoes_fiscais: 'Execução fiscal', outras_acoes: 'Outras ações', recursos: 'Recursos', recuperacao_judicial: 'Recuperação Judicial'};
async function copiar(txt, msg = 'Copiado') {
  try { await navigator.clipboard.writeText(txt); }
  catch (_) { const t = document.createElement('textarea'); t.value = txt; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t); t.select(); try { document.execCommand('copy'); } catch (__) { /* sem cópia */ } t.remove(); }
  toast(msg, 'ok');
}
const norm = t => String(t ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
function erroInfo(msg) {
  const [dica, tec] = String(msg || '').split(' Detalhe técnico: ');
  const T = [
    [/^Limite de uso/, 'Limite de uso do Gemini', ['Aguarde alguns minutos e use “Tentar novamente”.', 'No nível gratuito há limite de chamadas por minuto e por dia.', 'Se repetir, troque GEMINI_MODEL por um modelo mais leve.']],
    [/^Chave/, 'Chave do Gemini com problema', ['Confira GEMINI_API_KEY no arquivo .env.', 'Reinicie com docker compose up depois de corrigir.']],
    [/^Modelo/, 'Modelo indisponível', ['Ajuste GEMINI_MODEL no .env para um modelo listado no AI Studio.', 'Reinicie com docker compose up.']],
    [/^O Gemini não respondeu/, 'O Gemini demorou demais (504)', ['Use “Tentar novamente”: costuma ser passageiro.', 'Se repetir: modelo mais rápido (Flash / Flash-Lite) e GEMINI_THINKING=low.', 'Ou reduza o texto enviado em CONTEXT_BUDGET_CHARS.']],
    [/^O serviço/, 'Gemini sobrecarregado (503)', ['Tente novamente em alguns minutos.', 'Configure GEMINI_FALLBACK_MODEL para usar outro modelo nessas horas.']],
    [/^A IA devolveu/, 'Resposta fora do formato', ['Use “Tentar novamente”.']],
  ].find(([re]) => re.test(dica)) || [null, 'Falha na extração', ['Use “Tentar novamente”.', 'Se repetir, abra o detalhe técnico e me envie.']];
  return {titulo: T[1], dica, passos: T[2], tecnico: tec || (T[0] ? '' : dica)};
}
const dh = t => t ? new Date(t * 1000).toLocaleString('pt-BR', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '—';
function cardErro(l) {
  const e = erroInfo(l.error), quandoFalhou = l.ext?.fim ? `Falhou em ${dh(l.ext.fim)}${l.ext.inicio ? ` · tentou por ${fmtT(l.ext.fim - l.ext.inicio)}` : ''}` : '';
  return `<div class="errcard"><div class="ec-head"><span class="ec-ico" aria-hidden="true">⚠</span><div><b>${esc(e.titulo)}</b>${quandoFalhou ? `<div class="muted">${quandoFalhou}</div>` : ''}</div></div>
    <p>${esc(e.dica.split('. ').slice(0, 2).join('. ').replace(/\.?$/, '.'))}</p><ul>${e.passos.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
    <div class="acts">${l.docs.length ? '<button class="btn sm primary" data-again>⟳ Tentar novamente</button>' : ''}
      ${e.tecnico ? '<button class="btn sm" data-copy-err>Copiar detalhe técnico</button>' : ''}</div>
    ${e.tecnico ? `<details class="ec-tec"><summary>Detalhe técnico</summary><pre>${esc(e.tecnico)}</pre></details>` : ''}</div>`;
}
const editable = l => ['pronto', 'desatualizado'].includes(l.estado);
const row = n => data?.linhas.find(l => l.numero === n);

/* ---------- casos ---------- */
const quando = t => t ? new Date(t * 1000).toLocaleString('pt-BR', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';
async function loadCases(selectId) {
  const cs = await api('/api/cases');
  $('cases').innerHTML = cs.map(c => `<li class="case-card" tabindex="0" data-id="${c.id}">
      <div class="cc-top"><b class="cc-nome" title="${esc(c.nome)}">${esc(c.nome)}</b>
        <button class="icon-btn danger" data-del-case="${c.id}" title="Excluir este caso" aria-label="Excluir caso ${esc(c.nome)}">🗑</button></div>
      <div class="cc-stats"><span><b>${c.processos}</b> ${c.processos === 1 ? 'processo' : 'processos'}</span><span><b>${c.pdfs}</b> ${c.pdfs === 1 ? 'PDF' : 'PDFs'}</span><span><b>${c.extraidos}</b> ${c.extraidos === 1 ? 'extraído' : 'extraídos'}</span></div>
      <div class="cc-tags">${c.erros ? `<span class="tag err">${c.erros} com erro</span>` : ''}${c.em_andamento ? `<span class="tag blue">${c.em_andamento} em andamento</span>` : ''}${!c.erros && !c.em_andamento && c.pdfs ? '<span class="tag ok">em dia</span>' : ''}${!c.pdfs ? '<span class="tag">sem PDFs</span>' : ''}</div>
      <div class="cc-foot muted">Última atividade: ${quando(c.atividade)}</div></li>`).join('') || '<li class="case-empty">Nenhum caso ainda. Crie o primeiro acima.</li>';
  $('case-select').innerHTML = cs.map(c => `<option value="${c.id}" ${c.id === (selectId || caseId) ? 'selected' : ''}>${esc(c.nome)}</option>`).join('');
  casesData = cs;
  document.querySelectorAll('#cases li[data-id]').forEach(li => { li.onclick = () => openCase(li.dataset.id); li.onkeydown = e => e.key === 'Enter' && openCase(li.dataset.id); });
  document.querySelectorAll('[data-del-case]').forEach(b => b.onclick = e => { e.stopPropagation(); deleteCase(b.dataset.delCase); });
}
let casesData = [];
async function deleteCase(id) {
  const c = casesData.find(x => x.id === id); if (!c) return;
  if (!confirm(`Excluir o caso "${c.nome}"?\n\nIsso apaga ${c.processos} processo(s), ${c.pdfs} PDF(s), os resultados e a planilha-modelo deste caso.\nNão tem volta.`)) return;
  try {
    await api(`/api/cases/${id}`, {method: 'DELETE'}); toast('Caso excluído', 'ok');
    if (caseId === id) goHome(); else await loadCases();
  } catch (e) { toast(e.message, 'err'); }
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
$('case-del').onclick = () => caseId && deleteCase(caseId);
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
  $('tabs').innerHTML = data.abas.map(a => `<button role="tab" data-a="${a.key}" class="${a.key === aba ? 'on' : ''}" aria-selected="${a.key === aba}">${esc(a.titulo)}<b>${a.n}</b>${a.pendentes ? `<i class="pend-dot" title="${a.pendentes} sugestão(ões) da IA aguardando a sua autorização">${a.pendentes}</i>` : ''}</button>`).join('');
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
  if (filtro.st === 'ocultos') return l.oculto && (() => { const t = norm(filtro.q).split(/\s+/).filter(Boolean); return t.every(x => norm(`${l.numero} ${Object.values(l.valores).join(' ')}`).includes(x)); })();
  if (l.oculto) return false;  // ocultos só aparecem no filtro "Ocultos"
  if (filtro.st) {
    const ed = editable(l), ok = {revisar: ed && !l.reviewed, revisado: ed && l.reviewed, erro: l.estado === 'erro',
      andamento: ['extraindo', 'aguardando'].includes(l.estado) || l.docs.some(d => ['queued', 'processing'].includes(d.status)), sem_pdf: l.estado === 'sem_pdf', manual: l.estado === 'manual', sugestoes: l.sugestoes > 0}[filtro.st];
    if (!ok) return false;
  }
  const termos = norm(filtro.q).split(/\s+/).filter(Boolean); if (!termos.length) return true;
  const palha = norm(`${l.numero} ${Object.values(l.valores).join(' ')} ${l.rj ? JSON.stringify(l.rj) : ''}`); return termos.every(t => palha.includes(t));
}
const passaItem = i => { const termos = norm(filtro.q).split(/\s+/).filter(Boolean); const p = norm(Object.values(i).join(' ')); return termos.every(t => p.includes(t)); };
function updateFilterUI(shown, total) {
  const ativo = !!(filtro.q.trim() || filtro.st); $('f-clear').hidden = !ativo; $('f-count').textContent = ativo ? `Mostrando ${shown} de ${total}` : `${total} linha(s)`;
}
$('f-q').oninput = e => { filtro.q = e.target.value; if (data) renderGrid(); };
$('f-st').onchange = e => { filtro.st = e.target.value; if (data) renderGrid(); };
$('f-copy').onclick = () => {
  if (!data) return;
  const nums = (aba === 'bens' ? data.bens : aba === 'fraude' ? data.fraudes : data.linhas.filter(l => l.aba === aba).filter(passa)).map(x => x.numero);
  const unicos = [...new Set(nums)]; if (!unicos.length) return toast('Nenhum processo para copiar', 'err');
  copiar(unicos.join('\n'), `${unicos.length} número(s) copiado(s), um por linha`);
};
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
  $('grid-empty').hidden = true; $('grid-empty').className = 'grid-empty'; applyDens();
  if (aba === 'bens' || aba === 'fraude') {
    const lista = aba === 'bens' ? 'bens' : 'fraudes', todos = aba === 'bens' ? data.bens : data.fraudes, items = todos.filter(passaItem); updateFilterUI(items.length, todos.length);
    const pend = items.filter(i => i._st === 'sugerido').length, ST = {sugerido: ['blue', 'sugestão da IA'], autorizado: ['ok', 'autorizado']};
    table.innerHTML = gridHead(['__first', ...a.colunas.map(c => c.key), '__st'], [{text: 'Processo'}, ...a.colunas.map(c => ({text: c.titulo})), {text: 'Situação'}]) + `<tbody>${items.map((i, k) => {
      const [sc, stx] = ST[i._st] || ['', i._st], sug = i._st === 'sugerido';
      const cel = c => aba === 'fraude' && sug && (c.key === 'tipo' || c.key === 'analise')
        ? `<td><div class="cc"><select data-item-k="${c.key}"><option value=""></option>${data.listas[c.key === 'tipo' ? 'fraude_tipo' : 'fraude_analise'].map(o => `<option ${o === i[c.key] ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></div></td>`
        : `<td class="c ro"><div class="cc">${esc(i[c.key] ?? '')}</div></td>`;
      return `<tr data-item="${lista}|${esc(i.numero)}|${i._i}" class="${sug ? 'pend' : 'okrow'}"><td class="first"><div class="cc"><div class="numrow"><span class="idx">${k + 1}</span><span class="num">${esc(i.numero)}</span><button type="button" class="copy" data-copy="${esc(i.numero)}" title="Copiar o número do processo" aria-label="Copiar o número do processo">⧉</button></div>
        ${i.resumo && aba === 'fraude' ? `<div class="muted item-res">${esc(i.resumo)}</div>` : ''}${i.citacao ? `<div class="muted item-res">Origem: ${esc(i.citacao)}</div>` : ''}</div></td>
        ${a.colunas.map(cel).join('')}
        <td><div class="cc item-acts"><span class="tag ${sc}">${stx}</span>${sug ? '<button type="button" class="btn sm primary" data-item-act="autorizar" title="Envia este item para a planilha">✓ Autorizar</button><button type="button" class="btn sm" data-item-act="descartar" title="Não é o caso: tira da lista">✕ Descartar</button>'
          : '<button type="button" class="btn sm" data-item-act="sugerir" title="Volta a ser uma sugestão (sai da planilha)">Desfazer</button>'}</div></td></tr>`; }).join('')}</tbody>`;
    if (!items.length) emptyGrid(todos.length ? 'Nenhum item corresponde ao filtro' : aba === 'bens' ? 'Nenhum imóvel com auto de penhora ou registro encontrado ainda' : 'Nenhuma alegação de fraude encontrada ainda',
      todos.length ? 'Ajuste ou limpe o filtro acima.' : 'Quando a IA reconhece ' + (aba === 'bens' ? 'um imóvel (matrícula) com auto de penhora e registro' : 'uma alegação de fraude') + ' num processo, o item aparece aqui como sugestão. Só entra na planilha depois que você autorizar.');
    else if (pend) { const g = $('grid-empty'); g.hidden = false; g.className = 'grid-empty bar'; g.innerHTML = `<b>${pend} sugestão(ões) da IA aguardando a sua autorização.</b> Só o que for autorizado vai para a planilha exportada. <button type="button" class="btn sm primary" id="aut-todos">✓ Autorizar todas as ${pend}</button>`;
      $('aut-todos').onclick = () => autorizarTodos(lista, items.filter(i => i._st === 'sugerido')); }
    return;
  }
  updateFilterUI(linhas.length, todas.filter(l => !l.oculto).length);
  const pos = new Map(todas.filter(l => !l.oculto).map((l, i) => [l.numero, i + 1]));  // nº de ordem do processo na aba (não muda ao filtrar)
  const cols = aba === 'rj' ? RJ_COLS : a.colunas.filter(c => c.key !== 'numero');
  table.innerHTML = gridHead(['__first', ...cols.map(c => c.key)], [{text: 'Processo'}, ...cols.map(c => ({text: c.titulo, title: c.titulo}))]) + `<tbody>${linhas.map(l => {
    const [cls, txt] = ESTADO[l.estado], ed = editable(l), waiting = ['extraindo', 'aguardando'].includes(l.estado);
    const rc = (l.numero === sel ? 'sel ' : '') + (l.estado === 'erro' ? 'bad' : ed && l.reviewed ? 'okrow' : ed ? 'pend' : '');
    const cut = l.medidas?.contexto?.cortado ? `<span class="tag warn" title="A IA viu ${l.medidas.contexto.paginas_enviadas} de ${l.medidas.contexto.paginas_total} páginas (limite de contexto)">cortado</span>` : '';
    const warn = l.citacoes_invalidas.length ? `<span class="tag err" title="Citações que não existem no PDF: ${esc(l.citacoes_invalidas.join('; '))}">⚠ citação</span>` : '';
    const h = tam.alt[l.numero];
    return `<tr data-n="${esc(l.numero)}" class="${rc}"${h ? ` style="--rh:${h}px"` : ''}><td class="first"><div class="cc"><div class="numrow"><span class="idx" title="Nº de ordem nesta aba">${pos.get(l.numero) ?? '–'}</span><span class="num">${esc(l.numero)}</span><button type="button" class="copy" data-copy="${esc(l.numero)}" title="Copiar o número do processo" aria-label="Copiar o número do processo">⧉</button></div>
      <div class="sub"><span class="tag ${cls}">${txt}</span>${l.oculto ? '<span class="tag">oculto</span>' : ''}${l.sugestoes ? `<span class="tag blue" title="A IA sugeriu ${l.sugestoes} item(ns) para as abas Outros Bens / Fraudes: aguardam a sua autorização">💡 ${l.sugestoes}</span>` : ''}${cut}${warn}${ed ? `<label class="rev" title="Marcar como revisado"><input type="checkbox" data-rev ${l.reviewed ? 'checked' : ''}> <span>revisado</span></label>` : ''}</div></div>
      <span class="rzr" title="Arraste para ajustar a altura da linha · duplo clique restaura"></span></td>
      ${cols.map(c => cell(l, c, ed, waiting)).join('')}</tr>`;
  }).join('')}</tbody>`;
  if (!linhas.length) emptyGrid(todas.length ? 'Nenhum processo corresponde ao filtro' : 'Nenhum processo nesta aba', todas.length ? 'Ajuste ou limpe o filtro acima.' : 'Importe a planilha-modelo e envie PDFs (botão "Upload de Processo" no topo, ou arraste os arquivos para cá).');
}
function emptyGrid(t, p) { const g = $('grid-empty'); g.className = 'grid-empty'; g.hidden = false; g.innerHTML = `<b>${esc(t)}</b>${esc(p)}`; }
function ato(linha) {  // '<origem> - dd/mm/aaaa - descrição' -> data em negrito, origem discreta
  const m = String(linha).match(/^(.+?)\s+-\s+(\d{2}\/\d{2}\/\d{4}|data não informada)\s+-\s+(.+)$/);
  return m ? `<div class="li"><b>${esc(m[2])}</b> ${esc(m[3])} <span class="ori">${esc(m[1])}</span></div>` : `<div class="li">${esc(linha)}</div>`;
}
const dl = u => `/api/cases/${caseId}/processes/${enc(u)}`;
function rjCell(l, c, waiting) {  // aba Recuperação Judicial: resumo do relatório + abrir/baixar
  const W = inner => `<td><div class="cc">${inner}</div></td>`, r = l.rj;
  if (!r) return W(waiting ? '<div class="shim"></div>' : c.key === 'rj_rel' && l.estado === 'sem_pdf' ? '<div class="wait">Envie os PDFs da RJ</div>' : '<div class="wait">—</div>');
  const v = x => x ? esc(x) : '<span class="muted">—</span>';
  if (c.key === 'rj_emp') return W(`<div class="rj-c">${(r.empresas.slice(0, 4).map(e => `<div>${esc(e)}</div>`).join('')) || '<span class="muted">—</span>'}${r.empresas.length > 4 ? `<div class="mais">+${r.empresas.length - 4} empresa(s)</div>` : ''}</div>`);
  if (c.key === 'rj_vara') return W(`<div class="rj-c"><div>${v(r.vara)}</div><div class="muted">Administração judicial: ${v(r.administrador)}</div></div>`);
  if (c.key === 'rj_datas') return W(`<div class="rj-c"><div><span class="muted">Ajuizamento:</span> ${v(r.ajuizamento)}</div><div><span class="muted">Deferimento:</span> ${v(r.deferimento)}</div><div><span class="muted">Concessão:</span> ${v(r.concessao)}</div></div>`);
  if (c.key === 'rj_and') return W(`<div class="rj-c"><div class="rj-tags">${r.aprovado ? `<span class="tag ${r.aprovado === 'SIM' ? 'ok' : 'warn'}">Assembleia: ${esc(r.aprovado)}</span>` : ''}${r.homologado ? `<span class="tag ${r.homologado === 'SIM' ? 'ok' : 'warn'}">Homologado: ${esc(r.homologado)}</span>` : ''}${r.encerrada ? `<span class="tag">Encerrada: ${esc(r.encerrada)}</span>` : ''}${r.desagio != null ? `<span class="tag blue" title="Média do deságio de cada classe, ponderada pelo valor da classe no QGC">Deságio ponderado ${String(r.desagio).replace('.', ',')}%</span>` : ''}</div><div class="clamp rj-sit">${v(r.situacao)}</div></div>`);
  return W(`<div class="rj-c"><div class="acts"><button type="button" class="btn sm primary" data-rj-open="${esc(l.numero)}">📄 Abrir relatório</button><button type="button" class="btn sm" data-rj-dl="docx|${esc(l.numero)}">⬇ Word</button><button type="button" class="btn sm" data-rj-dl="pdf|${esc(l.numero)}">⬇ PDF</button></div>
    ${r.avisos ? `<span class="tag warn" title="Somas e totais que não fecham: abra o relatório">${r.avisos} ponto(s) a conferir</span>` : ''}<span class="muted">${r.nao_consta} campo(s) sem informação</span></div>`);
}
function cell(l, c, ed, waiting) {
  if (c.fonte === 'rj') return rjCell(l, c, ['extraindo', 'aguardando'].includes(l.estado));
  const v = l.valores[c.key] ?? '', tip = l.citacoes[c.key] ? ` title="Origem: ${esc(l.citacoes[c.key])}"` : '';
  const W = (inner, cls = '') => `<td${cls ? ` class="${cls}"` : ''}${tip}><div class="cc">${inner}</div></td>`;
  if (c.fonte !== 'ai') return W(esc(v), 'c ro');  // capa (Mcfly) e campos do sistema: somente leitura
  if (l.estado === 'manual') return W(c.key === 'observacoes' && v ? `<div class="manual-nota">${esc(v)}</div>` : '<div class="wait">—</div>');
  if (!ed) return W(waiting ? '<div class="shim"></div>' : '<div class="wait">—</div>');
  if (c.key === 'corresponsaveis') {  // lista: uma pessoa por linha, com CPF/CNPJ; o resto fica na janela
    const ls = String(v).split('\n').filter(Boolean);
    return W(`<div class="clamp lista" data-long="${c.key}" tabindex="0" role="button" title="Clique para ler e editar a lista inteira">${ls.slice(0, 3).map(x => `<div class="li">${esc(x)}</div>`).join('')}${ls.length > 3 ? `<div class="mais">+${ls.length - 3} na lista…</div>` : ''}</div>`);
  }
  if (c.key === 'observacoes' && String(v).includes('\n')) {  // cronologia: um ato por linha, com a data em destaque
    const ls = String(v).split('\n').filter(Boolean), max = tam.dens === 'compacto' ? 2 : tam.dens === 'amplo' ? 10 : 5;
    return W(`<div class="clamp lista atos" data-long="${c.key}" tabindex="0" role="button" title="Clique para ler e editar a lista inteira">${ls.slice(0, max).map(ato).join('')}${ls.length > max ? `<div class="mais">+${ls.length - max} ato(s) na lista…</div>` : ''}</div>`);
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
  if (e.target.matches('[data-item-k]')) { const [lista, numero, i] = tr.dataset.item.split('|'); return itemAcao(lista, numero, [+i], 'editar', {[e.target.dataset.itemK]: e.target.value || null}); }
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
  const cp = e.target.closest('[data-copy]'); if (cp) { e.stopPropagation(); return copiar(cp.dataset.copy, 'Número copiado'); }
  const ro = e.target.closest('[data-rj-open]'); if (ro) { e.stopPropagation(); return openRj(ro.dataset.rjOpen); }
  const rd = e.target.closest('[data-rj-dl]'); if (rd) { e.stopPropagation(); const [f, n] = rd.dataset.rjDl.split('|'); return void (location.href = `${dl(n)}/rj.${f}`); }
  const ia = e.target.closest('[data-item-act]'); if (ia) { const [lista, numero, i] = ia.closest('tr').dataset.item.split('|'); return itemAcao(lista, numero, [+i], ia.dataset.itemAct); }
  const tr = e.target.closest('tbody tr'); if (!tr || !tr.dataset.n) return;
  const lg = e.target.closest('[data-long]'); if (lg) openLong(tr.dataset.n, lg.dataset.long);
  if (e.target.closest('input[type=checkbox],label')) return;
  select(tr.dataset.n);
});
async function setReviewed(numero, v) {
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/review`, json('PUT', {reviewed: v})); row(numero).reviewed = v; dataKey = JSON.stringify(data); toast(v ? 'Marcado como revisado' : 'Marcado como a revisar', 'ok'); renderGrid(); renderAssistInfo(); if (v) await perguntarSugestoes(numero); }
  catch (e) { toast(e.message, 'err'); renderGrid(); }
}
/* texto longo (observações, corresponsáveis): janela para ler e editar tudo */
let longFor = null;
function openLong(numero, key) {
  const l = row(numero); if (!l || !editable(l)) return; const col = data.abas.flatMap(a => a.colunas).find(c => c.key === key);
  longFor = {numero, key}; $('notes-title').textContent = col ? col.titulo : 'Texto'; $('notes-sub').textContent = numero; $('notes-text').value = l.valores[key] ?? '';
  const c = l.medidas?.contexto, cr = l.cronologia;
  const corte = key !== 'observacoes' || !c?.cortado ? '' : c.modo === 'eventos' ? `⚠ ${c.atos_relevantes_omitidos} ato(s) relevante(s) ficaram sem o texto integral por falta de espaço: a cronologia pode estar incompleta. `
    : `⚠ A IA viu ${c.paginas_enviadas} de ${c.paginas_total} páginas deste processo: a cronologia pode estar incompleta. `;
  const conf = key === 'observacoes' && cr ? `Cronologia conferida com os ${cr.eventos_base} ${l.sistema === 'pje' ? 'documentos do índice do PJe' : 'eventos do eproc'}${cr.datas_corrigidas ? `: ${cr.datas_corrigidas} data(s) corrigida(s) pelo sistema` : ''}${cr.inexistentes.length ? `; ⚠ ato(s) que não existem: ${cr.inexistentes.join(', ')}` : ''}. ` : '';
  $('notes-cit').textContent = corte + conf + (l.citacoes[key] ? `Origem: ${l.citacoes[key]}` : ''); $('notes-fmt').hidden = !['corresponsaveis', 'observacoes'].includes(key); $('dlg-notes').showModal(); $('notes-text').focus();
}
$('notes-fmt').onclick = async () => {
  try { const r = await api(`/api/format/${longFor.key}`, json('POST', {texto: $('notes-text').value, case_id: caseId, numero: longFor.numero})); $('notes-text').value = r.texto; toast('Lista padronizada (clique em Salvar para gravar)'); } catch (e) { toast(e.message, 'err'); }
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
function sugCard(l) {  // sugestões da IA para as abas Outros Bens / Alegações de Fraude, à espera da autorização do analista
  if (!l.sugestoes || !data) return '';
  const sg = sugestoesDe(l.numero);
  const bot = (lista, i) => `<span class="sug-b"><button type="button" class="btn sm primary" data-sug="${lista}|${i}|autorizar">✓ Autorizar</button><button type="button" class="btn sm" data-sug="${lista}|${i}|descartar">✕</button></span>`;
  const fr = sg.fraudes.map(f => `<li><div><b>${esc(f.tipo || 'Fraude (tipo a definir)')}</b> · ${esc(f.analise || 'análise a definir')}${f.resumo ? `<div class="muted">${esc(f.resumo)}</div>` : ''}${f.citacao ? `<div class="muted">Origem: ${esc(f.citacao)}</div>` : ''}</div>${bot('fraudes', f._i)}</li>`).join('');
  const bn = sg.bens.map(b => `<li><div><b>${esc(b.ato || 'Ato')}</b> · ${esc(b.gravames || '')} <span class="muted">Matrícula ${esc(b.matricula || '—')}</span>${b.resumo ? `<div class="muted">${esc(b.resumo)}</div>` : ''}${b.citacao ? `<div class="muted">Origem: ${esc(b.citacao)}</div>` : ''}</div>${bot('bens', b._i)}</li>`).join('');
  return `<div class="sugcard"><b>💡 Sugestões da IA para as abas</b><div class="muted">Só entram na planilha depois da sua autorização.</div>
    ${fr ? `<div class="sug-t">Alegações de Fraude (${sg.fraudes.length}) <button type="button" class="btn sm" data-sug="fraudes|*|autorizar">Autorizar todas</button></div><ul>${fr}</ul>` : ''}
    ${bn ? `<div class="sug-t">Outros Bens (${sg.bens.length}) <button type="button" class="btn sm" data-sug="bens|*|autorizar">Autorizar todos</button></div><ul>${bn}</ul>` : ''}</div>`;
}
function renderAssistInfo() {
  const l = sel && row(sel); if (!l) { $('assist-empty').hidden = false; $('assist-body').hidden = true; $('assist-sub').textContent = 'Selecione uma linha'; return; }
  const [cls, txt] = ESTADO[l.estado], ed = editable(l);
  const pdfs = l.docs.map(d => { const [dc, dt] = DST[d.status] || ['', d.status];
    return `<div class="pdf"><span class="nm" title="${esc(d.filename)}">${esc(d.filename)}</span><span class="tag ${dc}">${dt}</span>
      ${d.started_at ? `<div class="muted pdf-h">${d.status === 'error' ? 'Falhou' : 'Lido'}: ${dh(d.started_at)} → ${dh(d.finished_at)}${d.finished_at ? ` (${fmtT(d.finished_at - d.started_at)})` : ''}</div>` : ''}${d.error ? `<div class="er" title="${esc(d.error)}">${esc(String(d.error).slice(0, 160))}${String(d.error).length > 160 ? '…' : ''}</div>` : ''}<button class="icon-btn" data-retry="${d.id}" title="Reprocessar este PDF" aria-label="Reprocessar">⟳</button><button class="icon-btn" data-del="${d.id}" title="Excluir este PDF" aria-label="Excluir">🗑</button></div>`; }).join('');
  const manualCard = l.manual?.ativo ? `<div class="alert info"><b>Feito manualmente.</b> ${l.manual.nota ? esc(l.manual.nota) : 'Sem observação.'}<br>A IA não mexe neste processo e a planilha registra isso nas observações.<br><button class="btn sm" data-manual-off>Desfazer marcação</button></div>` : '';
  $('assist-info').innerHTML = `<div class="info-top"><span class="num">${esc(l.numero)}</span><button type="button" class="copy on" data-copy-num title="Copiar o número do processo" aria-label="Copiar o número do processo">⧉</button><span class="tag ${cls}">${txt}</span>
      ${ed ? `<span class="tag ${l.reviewed ? 'ok' : 'blue'}">${l.reviewed ? 'revisado' : 'a revisar'}</span>` : ''}
      ${l.modelo ? `<span class="chip">${esc(MODELO[l.modelo] || l.modelo)}</span>` : ''}${l.sistema ? `<span class="chip">${esc(l.sistema)}</span>` : ''}${l.paginas ? `<span class="chip">${l.paginas} págs.</span>` : ''}${l.medidas ? `<span class="chip" title="Páginas enviadas à IA e tokens da extração">IA viu ${l.medidas.contexto.paginas_enviadas}/${l.medidas.contexto.paginas_total} págs. · ${fmtN(l.medidas.tokens?.entrada)} tokens</span>` : ''}</div>
    ${manualCard}
    ${sugCard(l)}
    ${l.error && !['extraindo', 'aguardando'].includes(l.estado) && !l.manual?.ativo ? cardErro(l) : ''}
    ${l.citacoes_invalidas.length ? `<div class="alert err"><b>Citações que não existem no PDF</b> (conferir): ${esc(l.citacoes_invalidas.join('; '))}</div>` : ''}
    ${l.modo === 'sem_llm' ? '<div class="alert warn">A IA não estava configurada: esta extração está vazia.</div>' : ''}
    <div class="acts">${ed ? `<button class="btn sm primary" data-toggle-rev>${l.reviewed ? '↺ Marcar como a revisar' : '✓ Marcar como revisado'}</button>` : ''}
      ${l.n_eventos ? `<button class="btn sm" data-eventos title="Todos os eventos lidos do PDF, sem IA">📅 Eventos (${l.n_eventos})</button>` : ''}
      ${!l.manual?.ativo ? '<button class="btn sm" data-manual-on title="Registra que este processo foi feito à mão: a IA não o extrai e a planilha mostra isso nas observações">✍ Feito manualmente</button>' : ''}
      ${l.docs.length && !l.manual?.ativo ? (l.aba === 'rj' && l.rj
        ? `<button class="btn sm primary" data-rj-upd title="Lê os PDFs de novo e COMPLETA o relatório: nada que já consta é apagado e as suas edições ficam protegidas">⟳ Atualizar com os PDFs</button><button class="btn sm" data-reextract title="Descarta o relatório e as suas edições e refaz tudo do zero">Refazer do zero</button>`
        : `<button class="btn sm" data-reextract title="Refaz a extração com todas as partes (descarta edições)">⟳ Refazer extração</button>`) : ''}</div>
    ${l.aba === 'rj' && l.rj ? `<div class="acts"><button class="btn sm primary" data-rj-open="${esc(l.numero)}">📄 Abrir relatório</button><button class="btn sm" data-rj-dl="docx|${esc(l.numero)}">⬇ Word</button><button class="btn sm" data-rj-dl="pdf|${esc(l.numero)}">⬇ PDF</button></div>` : ''}
    <div class="acts sec">${l.estado !== 'sem_pdf' || l.manual?.ativo ? '<button class="btn sm" data-hist title="Versões anteriores deste processo, com a opção de voltar">🕘 Histórico</button>' : ''}
      <button class="btn sm" data-ocultar title="${l.oculto ? 'Volta a aparecer na tabela e na planilha' : 'Tira da tabela e da planilha exportada, sem apagar nada'}">${l.oculto ? '👁 Reexibir' : '🙈 Ocultar'}</button>
      <button class="btn sm danger-btn" data-excluir title="Apaga o processo, os PDFs e o histórico dele">🗑 Excluir processo</button></div>
    <details class="pdfs" ${l.docs.some(d => d.status === 'error') ? 'open' : ''}><summary>PDFs deste processo (${l.docs.length})</summary>${pdfs || '<p class="muted">Nenhum PDF enviado para este processo.</p>'}</details>`;
  const q = (s) => $('assist-info').querySelector(s);
  q('[data-again]') && (q('[data-again]').onclick = () => reextract(l.numero, false));
  q('[data-copy-err]') && (q('[data-copy-err]').onclick = async () => { try { await navigator.clipboard.writeText(`${l.numero}\n${l.error}`); toast('Copiado', 'ok'); } catch (_) { toast('Não foi possível copiar', 'err'); } });
  $('assist-info').querySelectorAll('[data-rj-open]').forEach(b => b.onclick = () => openRj(b.dataset.rjOpen));
  $('assist-info').querySelectorAll('[data-rj-dl]').forEach(b => b.onclick = () => { const [f, n] = b.dataset.rjDl.split('|'); location.href = `${dl(n)}/rj.${f}`; });
  q('[data-hist]') && (q('[data-hist]').onclick = () => openHist(l.numero));
  q('[data-ocultar]') && (q('[data-ocultar]').onclick = () => setOculto(l.numero, !l.oculto));
  q('[data-excluir]') && (q('[data-excluir]').onclick = () => excluirProcesso(l.numero));
  $('assist-info').querySelectorAll('[data-sug]').forEach(b => b.onclick = () => { const [lista, i, acao] = b.dataset.sug.split('|'); itemAcao(lista, l.numero, i === '*' ? null : [+i], acao); });
  q('[data-copy-num]') && (q('[data-copy-num]').onclick = () => copiar(l.numero, 'Número copiado'));
  q('[data-manual-on]') && (q('[data-manual-on]').onclick = () => setManual(l.numero, true));
  q('[data-manual-off]') && (q('[data-manual-off]').onclick = () => setManual(l.numero, false));
  q('[data-reextract]') && (q('[data-reextract]').onclick = () => reextract(l.numero, true, l.aba === 'rj' ? 'zero' : undefined));
  q('[data-rj-upd]') && (q('[data-rj-upd]').onclick = () => reextract(l.numero, false, 'complementar'));
  q('[data-eventos]') && (q('[data-eventos]').onclick = () => openEventos(l.numero));
  q('[data-toggle-rev]') && (q('[data-toggle-rev]').onclick = () => setReviewed(l.numero, !l.reviewed));
  $('assist-info').querySelectorAll('[data-del]').forEach(b => b.onclick = () => delDoc(b.dataset.del));
  $('assist-info').querySelectorAll('[data-retry]').forEach(b => b.onclick = () => retryDoc(b.dataset.retry));
  $('q').disabled = !l.docs.some(d => d.status === 'done'); $('q').placeholder = $('q').disabled ? 'Envie e aguarde a leitura do PDF para perguntar' : 'Pergunte sobre este processo…';
}
async function itemAcao(lista, numero, indices, acao, valores) {
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/itens`, json('PUT', {lista, acao, indices, valores})); if (acao !== 'editar') toast({autorizar: 'Autorizado: vai para a planilha', descartar: 'Descartado', sugerir: 'Voltou a ser sugestão'}[acao], 'ok'); await refresh(true); return true; }
  catch (e) { toast(e.message, 'err'); refresh(true); return false; }
}
async function autorizarTodos(lista, itens) {
  if (!confirm(`Autorizar ${itens.length} item(ns)? Eles passam a constar da planilha exportada.`)) return;
  const porProc = {}; itens.forEach(i => (porProc[i.numero] ||= []).push(i._i));
  for (const [n, idx] of Object.entries(porProc)) await itemAcao(lista, n, idx, 'autorizar');
}
const sugestoesDe = numero => ({bens: data.bens.filter(i => i.numero === numero && i._st === 'sugerido'), fraudes: data.fraudes.filter(i => i.numero === numero && i._st === 'sugerido')});
async function perguntarSugestoes(numero) {  // ao marcar como revisado: pede a autorização para preencher as abas
  const sg = sugestoesDe(numero), nf = sg.fraudes.length, nb = sg.bens.length; if (!nf && !nb) return;
  const partes = [nf ? `${nf} alegação(ões) de fraude` : '', nb ? `${nb} ato(s) de matrícula com penhora/registro` : ''].filter(Boolean).join(' e ');
  if (!confirm(`A IA encontrou ${partes} neste processo.\n\nAutorizar o preenchimento das abas ${[nf ? '“Alegações de Fraude”' : '', nb ? '“Outros Bens”' : ''].filter(Boolean).join(' e ')}?\n\nOK = autorizar tudo · Cancelar = decidir depois (ficam como sugestão no painel e nas abas)`)) return;
  if (nf) await itemAcao('fraudes', numero, sg.fraudes.map(i => i._i), 'autorizar');
  if (nb) await itemAcao('bens', numero, sg.bens.map(i => i._i), 'autorizar');
}
async function setOculto(numero, oculto) {
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/hidden`, json('PUT', {oculto})); toast(oculto ? 'Processo oculto (veja em “Ocultos” no filtro de situação)' : 'Processo reexibido', 'ok'); if (oculto) { sel = null; renderAssistInfo(); } refresh(true); }
  catch (e) { toast(e.message, 'err'); }
}
async function excluirProcesso(numero) {
  const l = row(numero); if (!l) return;
  if (!confirm(`Excluir o processo ${numero}?\n\nIsso apaga ${l.docs.length} PDF(s), a extração e o histórico dele. Não tem volta.\n${tplState.ativo ? 'Se ele constar da planilha-modelo, volta (vazio) quando o modelo for enviado de novo; para tirá-lo só da tela e da exportação, use “Ocultar”.' : ''}`)) return;
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}`, {method: 'DELETE'}); toast('Processo excluído', 'ok'); sel = null; renderAssistInfo(); refresh(true); }
  catch (e) { toast(e.message, 'err'); }
}
/* ---------- relatório Mcfly: conferência (repetidos, divergentes, de outra parte) antes de importar ---------- */
let mc = {file: null, procs: [], esc: {}};
$('btn-mcfly').onclick = () => { mc = {file: null, procs: [], esc: {}}; $('mcfly-file').value = ''; $('mcfly-sum').innerHTML = ''; $('mcfly-tab').hidden = true; $('mcfly-filtros').hidden = true; $('mcfly-msg').textContent = ''; $('mcfly-count').textContent = ''; $('mcfly-ok').disabled = true; $('dlg-mcfly').showModal(); };
$('mcfly-file').onchange = async e => {
  const f = e.target.files[0]; if (!f) return; $('mcfly-msg').className = 'msg'; $('mcfly-msg').textContent = 'Lendo a planilha…';
  const fd = new FormData(); fd.append('file', f);
  try {
    const r = await api(`/api/cases/${caseId}/mcfly/preview`, {method: 'POST', body: fd});
    mc = {file: f, procs: r.processos.map(p => ({...p, on: p.marcar})), esc: {}}; $('mcfly-msg').textContent = '';
    $('mcfly-sum').innerHTML = `<div class="kpis" style="margin:8px 0">
      <div class="kpi"><span>Linhas na planilha</span><b>${r.linhas}</b><small>${r.processos.length} processo(s) diferente(s)</small></div>
      <div class="kpi"><span>Repetidos</span><b>${r.repetidos}</b><small>${r.conflitos ? `${r.conflitos} com valores divergentes` : 'reunidos em uma linha'}</small></div>
      <div class="kpi"><span>Sem ligação com o caso</span><b>${r.suspeitos}</b><small>${r.grupos.length} grupo(s) de partes na planilha</small></div>
      <div class="kpi"><span>Novos no caso</span><b>${r.novos}</b><small>${r.fora_do_mcfly.length} do caso não estão neste Mcfly</small></div></div>
      ${r.grupos.length > 1 ? `<div class="mc-grupos"><b>Grupos de partes encontrados</b> <span class="muted">(processos com alguma parte em comum, pelo CNPJ/CPF, ficam no mesmo grupo). Desmarque o grupo que não é deste caso:</span>
        ${r.grupos.map(g => `<label class="mc-g"><input type="checkbox" data-mc-g="${g.id}" ${g.do_caso ? 'checked' : ''}> <span><b>Grupo ${g.id}</b> · ${g.processos} processo(s) · ${g.partes} parte(s) ${g.cita_caso ? '<span class="tag ok">cita o nome do caso</span>' : ''}${g.no_caso ? `<span class="tag ok">${g.no_caso} já no caso</span>` : ''}${!g.do_caso ? '<span class="tag err">sem ligação com o caso</span>' : ''}<br><span class="muted">${esc(g.principais.join(' · '))}${g.partes > g.principais.length ? ' …' : ''}</span></span></label>`).join('')}</div>` : ''}
      <p class="muted">Onde houver valores divergentes, escolha o correto. Linhas repetidas do mesmo processo já foram reunidas, e as inscrições diferentes ficam juntas.</p>`;
    $('mcfly-tab').hidden = false; $('mcfly-filtros').hidden = false; $('mcfly-so').checked = false; renderMcfly();
  } catch (err) { $('mcfly-msg').className = 'msg err'; $('mcfly-msg').textContent = err.message; }
};
function renderMcfly() {
  const so = $('mcfly-so').checked, L = mc.procs.filter(p => !so || p.suspeito || Object.keys(p.conflitos).length || p.oculto);
  $('mcfly-tab').tBodies[0].innerHTML = L.map(p => { const i = mc.procs.indexOf(p), cf = p.conflitos.somatorio;
    const tags = [`<span class="tag" title="Grupo de partes">G${p.grupo}</span>`, p.suspeito ? '<span class="tag err" title="Nenhuma parte deste processo liga-o ao caso (nem pelo nome, nem por processo já cadastrado): pode ter vindo por engano">sem ligação</span>' : '',
      p.linhas > 1 ? `<span class="tag ${Object.keys(p.conflitos).length ? 'warn' : ''}" title="Este processo aparece em ${p.linhas} linhas do Mcfly">${p.linhas}× repetido</span>` : '',
      Object.keys(p.conflitos).filter(k => k !== 'somatorio').length ? `<span class="tag warn" title="As linhas repetidas divergem em: ${esc(Object.keys(p.conflitos).filter(k => k !== 'somatorio').join(', '))}. Vale a primeira.">dados divergentes</span>` : '',
      p.oculto ? '<span class="tag">oculto no caso</span>' : '', p.no_caso ? '<span class="tag ok">já no caso</span>' : '<span class="tag blue">novo</span>'].join(' ');
    return `<tr class="${p.suspeito ? 'bad' : ''}"><td><input type="checkbox" data-mc="${i}" ${p.on ? 'checked' : ''} aria-label="Importar ${esc(p.numero)}"></td>
      <td><span class="num">${esc(p.numero)}</span><div class="muted">${esc(p.classe || '')}</div></td>
      <td>${p.partes.map(x => `<div>${esc(x)}</div>`).join('') || '—'}${p.partes_mais ? `<div class="muted">+${p.partes_mais} parte(s)</div>` : ''}</td>
      <td style="white-space:pre-line">${esc(p.inscricoes || '—')}</td>
      <td>${cf ? `<select data-mc-som="${i}">${cf.map(v => `<option ${v === (mc.esc[p.numero]?.somatorio || p.somatorio) ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select><div class="muted">valores divergentes: escolha</div>` : esc(p.somatorio || '—')}</td>
      <td>${tags}</td></tr>`; }).join('') || '<tr><td colspan="6" class="muted">Nada pede atenção.</td></tr>';
  const n = mc.procs.filter(p => p.on).length; $('mcfly-count').textContent = `${n} de ${mc.procs.length} selecionado(s)`; $('mcfly-ok').disabled = !n; $('mcfly-ok').textContent = `Importar ${n} selecionado(s)`;
}
$('mcfly-tab').addEventListener('change', e => {
  if (e.target.matches('[data-mc]')) { mc.procs[+e.target.dataset.mc].on = e.target.checked; renderMcfly(); }
  if (e.target.matches('[data-mc-g]')) return;
  if (e.target.matches('[data-mc-som]')) { const p = mc.procs[+e.target.dataset.mcSom]; mc.esc[p.numero] = {somatorio: e.target.value}; }
});
$('mcfly-sum').addEventListener('change', e => {  // marcar/desmarcar um grupo inteiro
  if (!e.target.matches('[data-mc-g]')) return; const g = +e.target.dataset.mcG;
  mc.procs.forEach(p => { if (p.grupo === g) p.on = e.target.checked && !p.oculto; }); renderMcfly();
});
$('mcfly-so').onchange = renderMcfly;
$('mcfly-todos').onclick = () => { mc.procs.forEach(p => p.on = true); renderMcfly(); };
$('mcfly-nenhum').onclick = () => { mc.procs.forEach(p => p.on = false); renderMcfly(); };
$('mcfly-caso').onclick = () => { mc.procs.forEach(p => p.on = p.no_caso && !p.oculto); renderMcfly(); };
$('mcfly-ok').onclick = async () => {
  const sel = mc.procs.filter(p => p.on), susp = sel.filter(p => p.suspeito).length;
  if (susp && !confirm(`${susp} processo(s) selecionado(s) não têm ligação aparente com este caso. Importar mesmo assim?`)) return;
  const fd = new FormData(); fd.append('file', mc.file); fd.append('selecionados', JSON.stringify(sel.map(p => p.numero))); fd.append('escolhas', JSON.stringify(mc.esc));
  $('mcfly-ok').disabled = true;
  try { const r = await api(`/api/cases/${caseId}/mcfly`, {method: 'POST', body: fd}); toast(`Mcfly importado: ${r.atualizados} processo(s) atualizado(s), ${r.novos} novo(s)`, 'ok'); $('dlg-mcfly').close(); refresh(true); }
  catch (err) { $('mcfly-msg').className = 'msg err'; $('mcfly-msg').textContent = err.message; $('mcfly-ok').disabled = false; }
};

/* ---------- Recuperação Judicial: relatório editável (Word / PDF) ---------- */
let rjd = null, rjDirty = false, rjReg = [];
async function openRj(numero) {
  try { const r = await api(`${dl(numero)}/rj`); rjd = {...r, numero}; rjDirty = false; } catch (e) { return toast(e.message, 'err'); }
  $('rj-sub').textContent = numero; $('rj-rev').checked = rjd.reviewed; renderRj(); $('dlg-rj').showModal(); rjAuto();
}
const inp = (cls, attrs, v) => `<input class="${cls}" ${attrs} value="${esc(v ?? '')}">`;
function rjTabela(lista, cols, vazio) {  // tabela editável genérica: lista = array de objetos do relatório
  const id = rjReg.push({lista, cols}) - 1;
  return `<div class="rj-tab"><table class="plain"><thead><tr>${cols.map(c => `<th style="width:${c.w}%">${esc(c.t)}</th>`).join('')}<th></th></tr></thead><tbody>${lista.map((it, i) => `<tr>${cols.map(c => `<td>${c.sel
    ? `<select data-rj-t="${id}|${i}|${c.k}"><option value=""></option>${c.sel.map(o => `<option ${it[c.k] === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
    : inp('rj-in', `data-rj-t="${id}|${i}|${c.k}"${c.num ? ' inputmode="decimal"' : ''}`, it[c.k])}</td>`).join('')}<td><button type="button" class="icon-btn" data-rj-del="${id}|${i}" title="Remover linha" aria-label="Remover linha">✕</button></td></tr>`).join('') || `<tr><td colspan="${cols.length + 1}" class="muted">${esc(vazio)}</td></tr>`}</tbody></table>
    <button type="button" class="btn sm" data-rj-add="${id}">+ Adicionar linha</button></div>`;
}
function renderRj() {
  rjReg = []; const rel = rjd.rel = rjd.relatorio, c = rel.campos, calc = rjd.calculo;
  const campo = f => { const x = c[f.chave] ||= {valor: null, origem: null};
    const ent = f.tipo === 'paragrafo' ? `<textarea class="rj-ta" rows="3" data-rj-k="${f.chave}">${esc(x.valor ?? '')}</textarea>`
      : f.tipo === 'simnao' ? `<select data-rj-k="${f.chave}"><option value=""></option><option ${x.valor === 'SIM' ? 'selected' : ''}>SIM</option><option ${x.valor === 'NÃO' ? 'selected' : ''}>NÃO</option></select>`
      : inp('rj-in', `data-rj-k="${f.chave}" placeholder="${f.tipo === 'data' ? 'dd/mm/aaaa' : f.tipo === 'moeda' ? 'R$ 0,00' : ''}"`, x.valor);
    return `<div class="rj-f ${x.valor ? '' : 'vazio'}" title="${esc(f.dica)}"><label>${esc(f.rotulo)}${rjd.editados.includes('campo:' + f.chave) ? '<span class="tag" title="Você editou este campo: novos PDFs não o sobrescrevem">protegido</span>' : ''}</label>${ent}${inp('rj-in rj-or', `data-rj-o="${f.chave}" placeholder="Origem (ex.: ID 123, pg. 4)"`, x.origem)}</div>`; };
  const cabeca = `<div class="rj-resumo"><div class="kpis"><div class="kpi"><span>Deságio ponderado geral<i class="tip" tabindex="0" data-tip="Média do deságio de cada classe, ponderada pelo valor da classe no quadro de credores. Calculada pelo sistema a partir das tabelas abaixo.">*</i></span><b>${calc.desagio != null ? String(calc.desagio).replace('.', ',') + '%' : '—'}</b><small>${calc.base ? `base ${esc(calc.base)}` : 'preencha o valor e o deságio de cada classe'}</small></div>
    <div class="kpi"><span>Campos sem informação</span><b>${calc.nao_consta.length}</b><small>saem como “Não consta nos documentos analisados”</small></div>
    <div class="kpi"><span>Pontos a conferir</span><b>${calc.avisos.length + rjd.citacoes_invalidas.length}</b><small>somas e citações</small></div></div>
    ${rjd.complemento ? `<div class="alert info"><b>Relatório complementado com PDFs novos</b> (${dh(rjd.complemento.em)}): ${rjd.complemento.preenchidos} campo(s) preenchido(s), ${rjd.complemento.atualizados} atualizado(s), ${rjd.complemento.linhas_novas} linha(s) nova(s) nas tabelas${rjd.complemento.mantidos_por_edicao ? `, ${rjd.complemento.mantidos_por_edicao} mantido(s) porque você os editou` : ''}. Nada que já constava foi apagado.</div>` : ''}
    ${calc.avisos.map(a => `<div class="alert warn">${esc(a)}</div>`).join('')}${rjd.citacoes_invalidas.length ? `<div class="alert err"><b>Citações que não existem no PDF</b> (conferir): ${esc(rjd.citacoes_invalidas.join('; '))}</div>` : ''}</div>`;
  const tabs = {
    identificacao: () => `<h4>Empresas recuperandas</h4>${rjTabela(rel.empresas, [{k: 'nome', t: 'Empresa', w: 42}, {k: 'cnpj', t: 'CNPJ', w: 22}, {k: 'em_recuperacao_judicial', t: 'Consta “em recuperação judicial”?', w: 16, sel: ['SIM', 'NÃO']}, {k: 'origem', t: 'Origem', w: 16}], 'Nenhuma empresa informada.')}`,
    plano: () => `<h4>Classes de credores (valor no QGC e deságio, base do cálculo do deságio ponderado)</h4>${rjTabela(rel.classes, [{k: 'classe', t: 'Classe', w: 24}, {k: 'proposta', t: 'Proposta de pagamento (resumo)', w: 30}, {k: 'valor_sujeito', t: 'Valor sujeito (R$)', w: 18, num: 1}, {k: 'desagio_percentual', t: 'Deságio (%)', w: 10, num: 1}, {k: 'origem', t: 'Origem', w: 12}], 'Nenhuma classe informada.')}`,
    bens: () => `<h4>Bens do laudo de avaliação</h4>${rjTabela(rel.bens, [{k: 'tipo', t: 'Bem', w: 28}, {k: 'valor_mercado', t: 'Valor de mercado', w: 20, num: 1}, {k: 'valor_liquidacao', t: 'Liquidação forçada', w: 20, num: 1}, {k: 'localidade', t: 'Localidade', w: 16}, {k: 'origem', t: 'Origem', w: 16}], 'Nenhum bem informado.')}
      <h4>Quadros gerais de credores (QGC)</h4>${rel.qgc.map(q => `<div class="rj-qgc"><div class="rj-qh">${inp('rj-in', `data-rj-q="${rel.qgc.indexOf(q)}|fonte" placeholder="Fonte"`, q.fonte)}${inp('rj-in', `data-rj-q="${rel.qgc.indexOf(q)}|data" placeholder="dd/mm/aaaa"`, q.data)}${inp('rj-in', `data-rj-q="${rel.qgc.indexOf(q)}|total" placeholder="Total R$"`, q.total)}${inp('rj-in', `data-rj-q="${rel.qgc.indexOf(q)}|origem" placeholder="Origem"`, q.origem)}<button type="button" class="icon-btn" data-rj-delq="${rel.qgc.indexOf(q)}" title="Remover este quadro">✕</button></div>${rjTabela(q.classes, [{k: 'classe', t: 'Classe', w: 60}, {k: 'valor', t: 'Valor (R$)', w: 40, num: 1}], 'Sem classes.')}</div>`).join('') || '<p class="muted">Nenhum quadro informado.</p>'}
      <button type="button" class="btn sm" id="rj-addq">+ Adicionar quadro</button>`,
    observacoes: () => rjTabela(rel.observacoes, [{k: 'data', t: 'Data', w: 14}, {k: 'texto', t: 'Ato', w: 62}, {k: 'origem', t: 'Origem', w: 24}], 'Nenhum ato informado.'),
  };
  $('rj-body').innerHTML = cabeca + rjd.secoes.map(sc => `<section class="rj-sec"><h3>${esc(sc.titulo)}</h3>${sc.chave === 'observacoes' ? '' : rjd.campos.filter(f => f.secao === sc.chave).map(campo).join('')}${tabs[sc.chave] ? tabs[sc.chave]() : ''}</section>`).join('');
  rjAuto();
}
function rjAuto() {  // textareas crescem com o texto (só depois de a janela estar visível, senão a altura medida é 0)
  $('rj-body').querySelectorAll('textarea.rj-ta').forEach(t => { t.style.height = 'auto'; t.style.height = Math.max(64, Math.min(t.scrollHeight + 2, 320)) + 'px'; });
}
$('rj-body').addEventListener('input', e => {
  const t = e.target; rjDirty = true;
  if (t.matches('[data-rj-k]')) { rjd.rel.campos[t.dataset.rjK].valor = t.value || null; t.closest('.rj-f').classList.toggle('vazio', !t.value); if (t.matches('textarea')) { t.style.height = 'auto'; t.style.height = Math.max(64, Math.min(t.scrollHeight + 2, 320)) + 'px'; } }
  else if (t.matches('[data-rj-o]')) rjd.rel.campos[t.dataset.rjO].origem = t.value || null;
  else if (t.matches('[data-rj-t]')) { const [id, i, k] = t.dataset.rjT.split('|'), it = rjReg[+id].lista[+i]; it[k] = k === 'desagio_percentual' ? (t.value === '' ? null : t.value) : (t.value || null); }
  else if (t.matches('[data-rj-q]')) { const [i, k] = t.dataset.rjQ.split('|'); rjd.rel.qgc[+i][k] = t.value || null; }
});
$('rj-body').addEventListener('change', e => { if (e.target.matches('select[data-rj-t]')) e.target.dispatchEvent(new Event('input', {bubbles: true})); });
$('rj-body').addEventListener('click', e => {
  const add = e.target.closest('[data-rj-add]'), del = e.target.closest('[data-rj-del]'), dq = e.target.closest('[data-rj-delq]');
  if (add) { const r = rjReg[+add.dataset.rjAdd]; r.lista.push(Object.fromEntries(r.cols.map(c => [c.k, null]))); rjDirty = true; renderRj(); }
  if (del) { const [id, i] = del.dataset.rjDel.split('|'); rjReg[+id].lista.splice(+i, 1); rjDirty = true; renderRj(); }
  if (dq) { rjd.rel.qgc.splice(+dq.dataset.rjDelq, 1); rjDirty = true; renderRj(); }
  if (e.target.id === 'rj-addq') { rjd.rel.qgc.push({fonte: null, data: null, origem: null, total: null, classes: []}); rjDirty = true; renderRj(); }
});
async function rjSalvar() {
  try {
    const r = await api(`${dl(rjd.numero)}/rj`, json('PUT', {relatorio: rjd.rel, reviewed: $('rj-rev').checked}));
    rjDirty = false; toast('Relatório salvo', 'ok'); const n = await api(`${dl(rjd.numero)}/rj`); rjd = {...n, numero: rjd.numero}; renderRj(); refresh(true); return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}
$('rj-save').onclick = rjSalvar;
['rj-word', 'rj-pdf'].forEach(id => $(id).onclick = async e => { e.preventDefault(); if (rjDirty && !(await rjSalvar())) return; location.href = `${dl(rjd.numero)}/rj.${id === 'rj-word' ? 'docx' : 'pdf'}`; });
$('dlg-rj').addEventListener('cancel', e => { if (rjDirty && !confirm('Há alterações não salvas no relatório. Fechar mesmo assim?')) e.preventDefault(); });
$('dlg-rj').querySelectorAll('button[value=x]').forEach(b => b.addEventListener('click', e => { if (rjDirty && !confirm('Há alterações não salvas no relatório. Fechar mesmo assim?')) e.preventDefault(); }));

/* histórico de versões */
let histFor = null;
async function openHist(numero) {
  histFor = numero; $('hist-sub').textContent = numero; $('hist-list').innerHTML = '<p class="muted">Carregando…</p>'; $('dlg-hist').showModal();
  try {
    const r = await api(`/api/cases/${caseId}/processes/${enc(numero)}/history`);
    $('hist-list').innerHTML = r.versoes.map((v, i) => `<details class="hist" data-h="${v.id}"><summary><span class="tag ${i === 0 ? 'ok' : ''}">${i === 0 ? 'versão atual' : `versão ${r.versoes.length - i}`}</span> <b>${esc(v.origem)}</b> <span class="muted">${dh(v.at)}</span><div class="muted">${esc(v.resumo || '')}</div></summary><div class="hist-body muted">Abrindo…</div></details>`).join('')
      || '<p class="muted">Ainda não há versões guardadas. O histórico começa na próxima extração ou edição deste processo.</p>';
    $('hist-list').querySelectorAll('details').forEach((d, i) => d.addEventListener('toggle', async () => {
      if (!d.open || d.dataset.ok) return; d.dataset.ok = '1';
      try { const v = await api(`/api/cases/${caseId}/processes/${enc(numero)}/history/${d.dataset.h}`);
        d.querySelector('.hist-body').innerHTML = `<dl>${v.campos.map(c => `<dt>${esc(c.titulo)}</dt><dd>${esc(c.valor)}</dd>`).join('') || '<dd>Sem campos preenchidos.</dd>'}</dl><p>${v.reviewed ? 'Estava marcado como revisado.' : 'Estava como “a revisar”.'} ${v.bens} item(ns) em Outros Bens · ${v.fraudes} em Fraudes.</p>${i === 0 ? '' : '<button type="button" class="btn sm primary" data-restore>↩ Voltar a esta versão</button>'}`;
        const b = d.querySelector('[data-restore]'); if (b) b.onclick = async () => {
          if (!confirm('Voltar o processo a esta versão? A versão atual continua guardada no histórico.')) return;
          try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/history/${d.dataset.h}/restore`, {method: 'POST'}); toast('Versão restaurada', 'ok'); $('dlg-hist').close(); refresh(true); } catch (e) { toast(e.message, 'err'); } };
      } catch (e) { d.querySelector('.hist-body').textContent = e.message; }
    }));
  } catch (e) { $('hist-list').innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}
async function setManual(numero, ativo) {
  let nota = null;
  if (ativo) { nota = prompt('Marcar como feito manualmente.\nObservação (opcional), por exemplo: "Concluído no SAJ em 05/11":', ''); if (nota === null) return; }
  else if (!confirm('Desfazer a marcação? Se houver PDFs enviados, a IA volta a extrair este processo.')) return;
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/manual`, json('PUT', {manual: ativo, nota})); toast(ativo ? 'Marcado como feito manualmente' : 'Marcação desfeita', 'ok'); refresh(true); }
  catch (e) { toast(e.message, 'err'); }
}
async function reextract(numero, ask, modo) {
  if (ask && !confirm(modo === 'zero' ? 'Refazer o relatório DO ZERO? O relatório atual e as suas edições serão descartados.\n\nPara só acrescentar as informações dos PDFs novos, use “Atualizar com os PDFs”.' : 'Refazer a extração com todas as partes? As suas edições neste processo serão descartadas.')) return;
  try { await api(`/api/cases/${caseId}/processes/${enc(numero)}/reextract${modo ? `?modo=${modo}` : ''}`, {method: 'POST'}); toast(modo === 'complementar' ? 'Atualizando o relatório com os PDFs (nada será apagado)…' : 'Extração reiniciada'); refresh(true); } catch (e) { toast(e.message, 'err'); }
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
  const m = chats[sel][i], l = row(sel), num = sel; if (!l) return;
  if (!editable(l)) return toast('Este processo ainda não tem extração para receber observações.', 'err');
  try {  // a resposta entra já no formato da cronologia: 'Evento/ID, pg. - data - ato', na ordem das datas
    toast('Convertendo a resposta em atos da cronologia…');
    const r = await api(`/api/cases/${caseId}/processes/${enc(num)}/observacoes/assistente`, json('POST', {pergunta: m.q, resposta: m.text}));
    if (!r.adicionadas) {
      if (!confirm('A resposta não cita nenhum ato com origem (Evento/ID e página), então não dá para colocá-la no formato da cronologia.\n\nAdicionar mesmo assim, como texto, no fim das observações?')) return;
      const novo = `${(l.valores.observacoes || '').trim()}\n\n[Assistente · ${m.q}]\n${m.text.replace(/\*\*/g, '')}`.trim();
      await api(`/api/cases/${caseId}/processes/${enc(num)}/review`, json('PUT', {observacoes: novo, reviewed: false}));
      toast('Resposta adicionada como texto (marque como revisado após conferir)', 'ok');
    } else toast(`${r.adicionadas} ato(s) adicionados à cronologia${r.datas_do_sistema ? ', com a data do sistema' : ''}${r.inexistentes.length ? ` · ⚠ confira: ${r.inexistentes.join(', ')} não existe(m) no índice` : ''}. Marque como revisado após conferir.`, r.inexistentes.length ? 'err' : 'ok');
    await refresh(true);
  } catch (e) { toast(e.message, 'err'); }
}
$('chat').onsubmit = e => { e.preventDefault(); ask($('q').value); };

/* ---------- eventos do processo (eproc): linha do tempo fixa, sem IA ---------- */
let evCache = {numero: null, lista: []};
const REL = {alta: ['ok', 'relevante'], media: ['blue', 'a verificar'], baixa: ['', 'expediente']};
async function openEventos(numero) {
  try { const r = await api(`/api/cases/${caseId}/processes/${enc(numero)}/eventos`); evCache = {numero, lista: r.eventos}; }
  catch (e) { return toast(e.message, 'err'); }
  $('ev-sub').textContent = numero; $('ev-q').value = ''; $('ev-rel').checked = true; renderEventos(); $('dlg-ev').showModal();
}
function evFiltrados() {
  const t = norm($('ev-q').value).split(/\s+/).filter(Boolean), so = $('ev-rel').checked;
  return evCache.lista.filter(e => (!so || e.relevancia !== 'baixa') && t.every(x => norm(`${e.rotulo} ${e.data_fmt} ${e.movimento} ${e.tipo_documento} ${e.complemento} ${e.docs.join(' ')}`).includes(x)));
}
function renderEventos() {
  const L = evFiltrados(); $('ev-count').textContent = `${L.length} de ${evCache.lista.length} evento(s)`;
  $('ev-tab').tBodies[0].innerHTML = L.map(e => { const [c, t] = REL[e.relevancia];
    return `<tr><td><span class="num">${esc(String(e.evento))}</span></td><td>${esc(e.data_fmt)}</td><td>${esc(e.movimento || e.tipo_documento)}${e.complemento ? `<div class="muted">${esc(e.complemento)}</div>` : ''}</td>
      <td>${esc(e.docs.join(', ') || '—')}</td><td>${e.paginas || '—'}</td><td><span class="tag ${c}">${t}</span></td></tr>`; }).join('') || '<tr><td colspan="6" class="muted">Nenhum evento com esse filtro.</td></tr>';
}
$('ev-q').oninput = renderEventos; $('ev-rel').onchange = renderEventos;
$('ev-copy').onclick = async () => { const txt = evFiltrados().map(e => e.linha).join('\n'); try { await navigator.clipboard.writeText(txt); toast('Lista copiada', 'ok'); } catch (_) { toast('Não foi possível copiar: selecione e copie manualmente', 'err'); } };
$('ev-use').onclick = async () => {
  const L = evFiltrados(); if (!L.length) return; const l = row(evCache.numero);
  if (!l || !editable(l)) return toast('Este processo ainda não tem extração para receber observações. Use "Copiar lista".', 'err');
  if ((l.valores.observacoes || '').trim() && !confirm('Substituir as observações atuais por esta lista de eventos?')) return;
  await save(evCache.numero, 'observacoes', L.map(e => e.linha).join('\n\n'), null); $('dlg-ev').close();
};

/* ---------- medições: tokens, corte do contexto, tempos ---------- */
const fmtT = x => x == null ? '—' : x < 60 ? `${Math.round(x)} s` : `${Math.floor(x / 60)} min ${Math.round(x % 60)} s`;
const fmtN = n => n == null ? '—' : n >= 1e6 ? `${(n / 1e6).toFixed(2).replace('.', ',')} ${n >= 2e6 ? 'milhões' : 'milhão'}` : n >= 1000 ? `${(n / 1000).toFixed(1).replace('.', ',')} mil` : String(n);
function renderMed() {
  const todos = data.linhas, ls = todos.filter(l => l.medidas || l.docs.length), com = ls.filter(l => l.medidas), cortados = com.filter(l => l.medidas.contexto.cortado);
  const sum = (a, f) => a.reduce((t, x) => t + (f(x) || 0), 0), docs = ls.flatMap(l => l.docs.map(d => d.tempos).filter(Boolean));
  const tin = sum(com, l => l.medidas.tokens?.entrada), tout = sum(com, l => (l.medidas.tokens?.saida || 0) + (l.medidas.tokens?.raciocinio || 0));
  const ocrP = sum(docs, t => t.ocr_paginas), ocrS = sum(docs, t => t.ocr_s);
  const dc = data.documentos, st = x => dc.filter(d => d.status === x).length, naFila = dc.filter(d => d.status === 'queued');
  const barra = (partes, total) => `<div class="mbar">${partes.filter(x => x[0] > 0).map(([v, cor, t]) => `<i style="width:${Math.max(v / (total || 1) * 100, 1.5)}%;background:var(--${cor})" title="${esc(t)}"></i>`).join('')}</div>`;
  const leg = partes => `<div class="mleg">${partes.map(([v, cor, t]) => `<span><i style="background:var(--${cor})"></i>${esc(t)} <b>${v}</b></span>`).join('')}</div>`;
  const iaS = sum(com, l => l.medidas.extracao_s), leS = sum(docs, t => t.total_s), medIa = com.length ? iaS / com.length : 0;
  const manuais = ls.filter(l => l.estado === 'manual').length, comPdf = ls.length, semExt = Math.max(comPdf - com.length, 0), inteiros = com.length - cortados.length;
  const pdfP = [[st('done'), 'ok', 'lidos'], [st('processing'), 'blue', 'lendo'], [st('queued'), 'muted', 'na fila'], [st('error'), 'err', 'com erro']];
  const procP = [[inteiros, 'ok', 'IA viu tudo'], [cortados.length, 'warn', 'cortados'], [Math.max(semExt - manuais, 0), 'muted', 'sem extração'], [manuais, 'blue', 'feitos manualmente']];
  const tmpP = [[Math.round(leS), 'blue', 'leitura dos PDFs (' + fmtT(leS) + ')'], [Math.round(iaS), 'warn', 'IA (' + fmtT(iaS) + ')']];
  const tokP = [[tin, 'blue', 'entrada'], [tout, 'warn', 'saída']];
  const porDia = medIa ? Math.floor(8 * 3600 / (medIa + (docs.length ? leS / docs.length : 0))) : null;
  const kpi = (rot, val, sub, dica) => `<div class="kpi"><span>${rot}${dica ? `<i class="tip" tabindex="0" data-tip="${esc(dica)}">*</i>` : ''}</span><b>${val}</b><small>${sub}</small></div>`;
  $('med-sum').innerHTML = `<div class="kpis">
      ${kpi('Processos extraídos', `${com.length}<em> de ${todos.length}</em>`, `${comPdf} com PDF enviado`, 'Processos cuja extração por IA terminou, de todos os processos do caso (incluindo os da planilha que ainda não têm PDF).')}
      ${kpi('Tokens gastos', fmtN(tin + tout), `${fmtN(tin)} entrada · ${fmtN(tout)} saída`, 'Token é o pedaço de texto que a IA lê e escreve (em português, cerca de 2,5 caracteres). Entrada: o que enviamos (processo + prompt). Saída: o que a IA devolve (inclui o raciocínio). É a base da cobrança e dos limites do provedor.')}
      ${kpi('Tempo médio por processo', com.length ? fmtT(medIa) : '—', 'só a chamada à IA', 'Média do tempo da chamada à IA por processo extraído. Não inclui a leitura/OCR dos PDFs. Esperas por erro 503 ou limite de uso do provedor entram na conta.')}
      ${kpi('Ritmo estimado', porDia ? `~${porDia}<em> /8 h</em>` : '—', 'um processo por vez; baseado nas medições', 'Projeção de quantos processos cabem em 8 horas, um de cada vez: 8 h ÷ (tempo médio da IA + tempo médio de leitura do PDF). É uma estimativa e muda com o modelo e o tamanho dos processos.')}</div>
    <div class="mrows">
      <div><div class="mt"><span>PDFs<i class="tip" tabindex="0" data-tip="Arquivos enviados neste caso: lidos com sucesso, em leitura pelo worker, na fila ou com erro.">*</i></span><b>${dc.length}</b></div>${barra(pdfP, dc.length)}${leg(pdfP)}</div>
      <div><div class="mt"><span>Processos com PDF<i class="tip" tabindex="0" data-tip="A IA viu tudo: o processo coube no limite enviado. Cortados: passou do limite e parte das páginas ficou de fora. Sem extração: aguardando ou com falha.">*</i></span><b>${comPdf}</b></div>${barra(procP, comPdf)}${leg(procP)}</div>
      <div><div class="mt"><span>Tempo total<i class="tip" tabindex="0" data-tip="Soma do tempo de máquina: leitura dos PDFs (texto e OCR) + tempo de resposta e espera da IA. Não conta o tempo em fila.">*</i></span><b>${fmtT(leS + iaS)}</b></div>${barra(tmpP, leS + iaS)}${leg(tmpP)}<div class="muted">OCR: ${ocrP} página(s) em ${fmtT(ocrS)}</div></div>
      <div><div class="mt"><span>Tokens<i class="tip" tabindex="0" data-tip="Volume consumido na API (cerca de 2,5 caracteres por token). Entrada = prompt + páginas do PDF; saída = o que a IA devolveu.">*</i></span><b>${fmtN(tin + tout)}</b></div>${barra(tokP, tin + tout)}${leg(tokP)}</div></div>
    ${!com.length ? '<div class="alert warn">Ainda não há extrações com medição. Processos extraídos antes desta versão não têm esses dados: use “Refazer extração”.</div>' : ''}`;
  const dur = (a, b) => a && b ? fmtT(b - a) : '—';
  const reg = [];
  for (const d of data.documentos) if (d.started_at) reg.push({t: d.started_at, tipo: 'Leitura do PDF', item: d.filename, ini: d.started_at, fim: d.finished_at,
    res: d.status === 'error' ? ['err', 'erro'] : d.status === 'done' ? ['ok', 'êxito'] : ['blue', 'em andamento'], det: d.status === 'error' ? d.error : ''});
  for (const l of todos) if (l.ext?.inicio) reg.push({t: l.ext.inicio, tipo: 'Extração por IA', item: l.numero, ini: l.ext.inicio, fim: l.ext.fim,
    res: l.ext.status === 'erro' ? ['err', 'erro'] : l.ext.status === 'ok' ? ['ok', 'êxito'] : ['blue', 'em andamento'], det: l.ext.status === 'erro' ? l.error : ''});
  reg.sort((a, b) => b.t - a.t);
  $('med-log').tBodies[0].innerHTML = reg.map(r => `<tr><td>${r.tipo}</td><td><span class="num">${esc(r.item)}</span></td><td>${dh(r.ini)}</td><td>${dh(r.fim)}</td><td>${dur(r.ini, r.fim)}</td>
    <td><span class="tag ${r.res[0]}">${r.res[1]}</span>${r.det ? `<div class="muted" title="${esc(r.det)}">${esc(String(r.det).slice(0, 90))}${String(r.det).length > 90 ? '…' : ''}</div>` : ''}</td></tr>`).join('')
    || '<tr><td colspan="6" class="muted">Ainda não há registros. Os PDFs enviados a partir desta versão passam a ser anotados aqui.</td></tr>';
  $('med-tab').tBodies[0].innerHTML = ls.map(l => { const m = l.medidas, c = m?.contexto, tk = m?.tokens, d = l.docs.map(x => x.tempos).filter(Boolean);
    return `<tr><td><span class="num">${esc(l.numero)}</span></td><td>${c ? `${c.paginas_total}${barra([[c.paginas_enviadas, c.cortado ? 'warn' : 'ok', 'enviadas à IA'], [Math.max(c.paginas_total - c.paginas_enviadas, 0), 'line', 'não enviadas']], c.paginas_total)}` : (l.paginas ?? '—')}</td>
      <td>${c && c.modo === 'eventos' ? `texto integral de ${c.eventos_com_texto} de ${c.eventos_total} eventos ${c.cortado ? '<span class="tag warn">cortado</span>' : '<span class="tag ok">completo</span>'}<div class="muted">${c.paginas_enviadas} de ${c.paginas_total} págs. · expediente só no índice${m.cronologia ? ` · ${m.cronologia.datas_corrigidas} data(s) corrigida(s)` : ''}</div>`
        : c ? `${c.paginas_enviadas} de ${c.paginas_total} ${c.cortado ? '<span class="tag warn">cortado</span>' : '<span class="tag ok">inteiro</span>'}<div class="muted">${fmtN(c.caracteres_enviados)} de ${fmtN(c.caracteres_total)} caracteres</div>` : '—'}</td>
      <td>${tk ? `${fmtN(tk.entrada)} / ${fmtN(tk.saida)}${tk.raciocinio ? `<div class="muted">+ ${fmtN(tk.raciocinio)} de raciocínio</div>` : ''}` : '—'}</td><td>${m ? `${fmtT(m.extracao_s)}${m.chamada ? `<div class="muted" title="Modelo que respondeu: ${esc(m.chamada.modelo)}${m.chamada.tempos?.length ? ` · tentativas: ${m.chamada.tempos.map(t => `${Math.round(t.s)} s (${t.r})`).join(', ')}` : ''}">${m.chamada.tentativas} tentativa(s)${m.chamada.espera_s ? ` · ${fmtT(m.chamada.espera_s)} de espera` : ''}</div>` : ''}` : '—'}</td>
      <td>${d.length ? `${fmtT(sum(d, t => t.total_s))}<div class="muted">OCR: ${sum(d, t => t.ocr_paginas)} pág. em ${fmtT(sum(d, t => t.ocr_s))}</div>` : '—'}</td></tr>`; }).join('') || '<tr><td colspan="6" class="muted">Nenhum processo com PDF enviado ainda.</td></tr>';
}
$('btn-med').onclick = () => { renderMed(); $('med-pdf').href = `/api/cases/${caseId}/medicoes.pdf?tz=${new Date().getTimezoneOffset()}`; $('dlg-med').showModal(); };

/* ---------- janelas: upload, modelo, PDFs ---------- */
const CNJ = /\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}/;
let ignorados = 0;
function renderPicked() {  // agrupa por nº do processo lido do nome do arquivo
  const grupos = new Map(); picked.forEach((f, i) => { const n = (f.name.match(CNJ) || [])[0] || ''; (grupos.get(n) || grupos.set(n, []).get(n)).push([f, i]); });
  const ordem = [...grupos.keys()].sort((a, b) => !a - !b || a.localeCompare(b));
  const mb = picked.reduce((t, f) => t + f.size, 0) / 1048576;
  $('picked-sum').innerHTML = picked.length ? `<b>${picked.length}</b> PDF(s) · <b>${ordem.filter(Boolean).length}</b> processo(s) identificado(s) pelo nome · ${mb.toFixed(1)} MB${grupos.has('') ? ` · <span class="tag warn">${grupos.get('').length} sem nº no nome</span>` : ''}${ignorados ? ` · ${ignorados} arquivo(s) que não são PDF foram ignorados` : ''} <button type="button" class="btn sm" id="picked-clear">Limpar</button>` : '';
  const pc = $('picked-clear'); if (pc) pc.onclick = () => { picked = []; ignorados = 0; renderPicked(); };
  $('picked').innerHTML = ordem.map(n => `<li class="grp"><div class="grp-h">${n ? `<span class="num">${esc(n)}</span>` : '<span class="tag warn">Sem nº no nome</span> <span class="muted">o sistema tenta achar o nº no texto do PDF</span>'} <span class="muted">${grupos.get(n).length} arquivo(s)</span></div>
    ${grupos.get(n).sort((a, b) => a[0].name.localeCompare(b[0].name, 'pt', {numeric: true})).map(([f, i]) => `<div class="arq"><span>📄 ${esc(f.name)} <span class="muted">(${(f.size / 1048576).toFixed(1)} MB)</span></span><button type="button" class="x" data-x="${i}" aria-label="Remover">✕</button></div>`).join('')}</li>`).join('');
  document.querySelectorAll('#picked [data-x]').forEach(b => b.onclick = () => { picked.splice(+b.dataset.x, 1); renderPicked(); });
  $('pdfs-btn').disabled = !picked.length; $('pdfs-btn').textContent = picked.length ? `Enviar ${picked.length} PDF(s)` : 'Enviar PDFs';
}
const addFiles = fl => {
  const todos = [...fl], pdfs = todos.filter(f => f.name.toLowerCase().endsWith('.pdf')); ignorados += todos.length - pdfs.length;
  const tem = new Set(picked.map(f => `${f.name}|${f.size}`));  // não repete o mesmo arquivo
  picked = [...picked, ...pdfs.filter(f => !tem.has(`${f.name}|${f.size}`) && tem.add(`${f.name}|${f.size}`))]; renderPicked();
};
async function arquivosDoDrop(dt) {  // aceita arquivos soltos e PASTAS inteiras (com subpastas)
  const entradas = [...(dt.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entradas.some(e => e.isDirectory)) return [...dt.files];
  const out = [];
  const ler = async e => {
    if (e.isFile) return out.push(await new Promise((ok, no) => e.file(ok, no)));
    const r = e.createReader(); let lote;
    do { lote = await new Promise((ok, no) => r.readEntries(ok, no)); for (const x of lote) await ler(x); } while (lote.length);
  };
  for (const e of entradas) { try { await ler(e); } catch (_) { /* pasta sem permissão: segue */ } }
  return out;
}
function openUpload(files) { if (files) addFiles(files); $('msg').textContent = ''; $('msg').className = 'msg'; if (!$('dlg-upload').open) $('dlg-upload').showModal(); }
$('btn-upload').onclick = () => openUpload();
$('drop').onclick = () => $('pdfs').click();
$('drop').onkeydown = e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), $('pdfs').click());
$('pdfs').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
$('pasta-btn').onclick = e => { e.stopPropagation(); $('pasta').click(); };
$('pasta').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
['dragenter', 'dragover'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => $('drop').addEventListener(ev, e => { e.preventDefault(); $('drop').classList.remove('over'); }));
$('drop').addEventListener('drop', async e => addFiles(await arquivosDoDrop(e.dataTransfer)));
// arrastar PDFs direto para a planilha abre o envio
const gw = $('grid-wrap');
['dragenter', 'dragover'].forEach(ev => gw.addEventListener(ev, e => { if (!caseId) return; e.preventDefault(); dragging = true; gw.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => gw.addEventListener(ev, e => { e.preventDefault(); dragging = false; gw.classList.remove('over'); }));
gw.addEventListener('drop', async e => { if (!caseId) return; const fs = await arquivosDoDrop(e.dataTransfer); if (fs.length) openUpload(fs); });
$('pdfs-btn').onclick = async () => {
  if (!picked.length) return; $('pdfs-btn').disabled = true; $('msg').className = 'msg'; let n = 0; const falhas = [], total = picked.length;
  for (const f of [...picked]) {  // um por vez; uma falha não interrompe o lote
    $('msg').textContent = `Enviando ${++n}/${total}: ${f.name}…`; $('up-bar').hidden = false; $('up-bar').firstElementChild.style.width = `${n / total * 100}%`;
    const fd = new FormData(); fd.append('file', f); fd.append('case_id', caseId); fd.append('tipo', $('tipo').value); fd.append('sistema', $('sistema').value);
    try { await api('/api/documents', {method: 'POST', body: fd}); picked = picked.filter(x => x !== f); }
    catch (e) { falhas.push(`${f.name}: ${e.message}`); }
  }
  $('up-bar').hidden = true; ignorados = 0; renderPicked(); refresh(true);
  if (falhas.length) { $('msg').className = 'msg err'; $('msg').textContent = `${total - falhas.length} enviado(s), ${falhas.length} com falha (continuam na lista para tentar de novo): ${falhas.slice(0, 3).join(' | ')}${falhas.length > 3 ? ' …' : ''}`; return; }
  toast(`${total} PDF(s) enviado(s). A leitura segue em segundo plano.`, 'ok'); $('dlg-upload').close();
};
function showTemplate(t) {
  tplState = t; $('tpl-del').hidden = !t.ativo; $('tpl-dot').hidden = !t.ativo;
  if (!t.ativo) { $('tpl-report').innerHTML = '<p class="muted">Sem modelo: a tabela e a planilha exportada usam o layout padrão.</p>'; return; }
  const abas = (t.abas || []).map(a => a.preenchida
    ? `<li>✔ <b>${esc(a.aba)}</b>: ${a.reconhecidas.length} coluna(s) reconhecida(s)${a.nao_reconhecidas.length ? `<br><span class="tag warn">Ficam em branco</span> ${esc(a.nao_reconhecidas.join(' · '))}` : ''}</li>`
    : `<li><span class="tag warn">Atenção</span> <b>${esc(a.aba)}</b>: ${esc(a.motivo)}</li>`).join('');
  $('tpl-report').innerHTML = `<ul>${abas}</ul>` + ((t.ausentes || []).length ? `<div class="alert warn">O modelo não tem a(s) aba(s): ${esc(t.ausentes.join(', '))}. Processos desse tipo aparecem só em Relatório.</div>` : '')
    + (t.dicas || []).map(d => `<p class="muted">💡 ${esc(d)}</p>`).join('')
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
let docsSel = new Set();
function openDocs() { docsSel = new Set(); renderDocs(); $('dlg-docs').showModal(); }
$('btn-docs').onclick = openDocs;
function renderDocs() {
  if (!data) return;
  const D = data.documentos; docsSel = new Set([...docsSel].filter(id => D.some(d => d.id === id)));  // some o que já foi excluído
  const n = st => D.filter(d => d.status === st).length;
  $('docs-sum').innerHTML = `<b>${D.length}</b> PDF(s) · ${n('done')} lido(s) · ${n('queued') + n('processing')} em andamento · <b>${n('error')}</b> com erro`;
  $('docs').tBodies[0].innerHTML = D.map(d => { const [dc, dt] = DST[d.status] || ['', d.status];
    return `<tr class="${d.status === 'error' ? 'bad' : ''}"><td><input type="checkbox" data-doc="${d.id}" ${docsSel.has(d.id) ? 'checked' : ''} aria-label="Selecionar ${esc(d.filename)}"></td><td>${esc(d.filename)}</td><td><span class="tag ${dc}">${dt}</span></td><td>${d.pages_done}/${d.pages_total}${d.pages_ocr ? ` <span class="muted">(${d.pages_ocr} OCR)</span>` : ''}</td>
      <td${d.error ? ` title="${esc(d.error)}"` : ''}>${esc(d.error ? String(d.error).split(' Detalhe técnico: ')[0] : (d.numero_processo ? 'Processo ' + d.numero_processo : 'nº do processo não identificado'))}</td>
      <td>${['error', 'processing', 'queued'].includes(d.status) ? `<button type="button" class="btn sm" data-retry="${d.id}">Reprocessar</button> ` : ''}<button type="button" class="btn sm danger-ghost" data-del="${d.id}">Excluir</button></td></tr>`; }).join('')
    || '<tr><td colspan="6" class="muted">Nenhum PDF enviado.</td></tr>';
  $('docs').querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { await delDoc(b.dataset.del); renderDocs(); });
  $('docs').querySelectorAll('[data-retry]').forEach(b => b.onclick = async () => { await retryDoc(b.dataset.retry); renderDocs(); });
  $('docs-sel-err').disabled = !n('error'); docsBar();
}
function docsBar() {
  const k = docsSel.size, D = data.documentos;
  $('docs-count').textContent = k ? `${k} selecionado(s)` : ''; $('docs-del').disabled = $('docs-retry').disabled = !k;
  $('docs-del').textContent = k ? `🗑 Excluir ${k} selecionado(s)` : '🗑 Excluir selecionados';
  $('docs-all').checked = !!D.length && k === D.length; $('docs-all').indeterminate = k > 0 && k < D.length;
}
$('docs').addEventListener('change', e => {
  if (e.target.id === 'docs-all') { docsSel = new Set(e.target.checked ? data.documentos.map(d => d.id) : []); return renderDocs(); }
  if (e.target.matches('[data-doc]')) { e.target.checked ? docsSel.add(e.target.dataset.doc) : docsSel.delete(e.target.dataset.doc); docsBar(); }
});
$('docs-sel-err').onclick = () => { docsSel = new Set(data.documentos.filter(d => d.status === 'error').map(d => d.id)); renderDocs(); };
$('docs-sel-none').onclick = () => { docsSel = new Set(); renderDocs(); };
async function docsLote(acao) {
  const ids = [...docsSel]; if (!ids.length) return;
  const sel = data.documentos.filter(d => docsSel.has(d.id)), lidos = sel.filter(d => d.status === 'done').length;
  if (acao === 'excluir' && !confirm(`Excluir ${ids.length} PDF(s)?${lidos ? `\n\n${lidos} deles já foram lidos: os processos correspondentes serão reextraídos sem esses arquivos.` : '\n\nNenhum deles foi lido com sucesso, então nenhuma extração muda.'}\nNão tem volta.`)) return;
  try {
    const r = await api('/api/documents/lote', json('POST', {ids, acao}));
    toast(acao === 'excluir' ? `${r.feitos} PDF(s) excluído(s)` : `${r.feitos} PDF(s) reenviado(s) para a fila`, 'ok'); docsSel = new Set(); await refresh(true); renderDocs();
  } catch (e) { toast(e.message, 'err'); }
}
$('docs-del').onclick = () => docsLote('excluir');
$('docs-retry').onclick = () => docsLote('reprocessar');

/* ---------- tema claro/escuro ---------- */
function applyTheme(t) { document.documentElement.dataset.theme = t; store.set('tema', t); $('theme').textContent = t === 'dark' ? '☀' : '🌙'; $('theme').title = t === 'dark' ? 'Mudar para tema claro' : 'Mudar para tema escuro'; }
$('theme').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
applyTheme(document.documentElement.dataset.theme || 'light');

/* ---------- inicialização ---------- */
api('/api/tipos').then(t => { schema = t; for (const [k, v] of Object.entries(t.tipos)) $('tipo').add(new Option(v, k)); for (const [k, v] of Object.entries(t.sistemas)) $('sistema').add(new Option(v, k)); });
api('/api/health').then(h => {
  $('llm').textContent = h.llm ? `IA ativa · ${h.modelo || h.llm}` : 'IA desativada (sem chave)';
  $('llm').title = `Versão ${h.versao} · modelo ${h.modelo || '—'}${h.reserva ? ` (reserva ${h.reserva})` : ''} · raciocínio ${h.raciocinio || 'padrão'} · contexto ${fmtN(h.contexto_chars)} caracteres`;
  const v = $('versao'); if (v) v.textContent = `Versão ${h.versao}`;
}).catch(() => {});
loadCases();
