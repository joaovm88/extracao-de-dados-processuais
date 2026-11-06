// static/app.js
/**
 * Gerenciamento de estado enxuto em Vanilla JS (Fluxo Unidirecional).
 */
const state = {
  columns: [],
  processes: [],
  selectedProcessId: null,
};

// Referências de elementos do DOM
const els = {
  tableHeaders: document.getElementById("table-headers"),
  tableBody: document.getElementById("table-body"),
  uploadForm: document.getElementById("upload-form"),
  pdfFileInput: document.getElementById("pdf-file"),
  fileLabelText: document.getElementById("file-label-text"),
  categorySelect: document.getElementById("process-category"),
  btnUpload: document.getElementById("btn-upload"),
  btnExport: document.getElementById("btn-export"),
  activeProcessLabel: document.getElementById("active-process-label"),
  chatMessages: document.getElementById("chat-messages"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  btnSendChat: document.getElementById("btn-send-chat"),
  quickChips: document.querySelectorAll(".chip"),
};

/**
 * 1. Inicialização da Aplicação
 */
async function initWorkspace() {
  await fetchTemplateColumns();
  await fetchProcesses();
  bindEvents();
}

/**
 * Busca as colunas corporativas definidas em app/template.py
 */
async function fetchTemplateColumns() {
  const res = await fetch("/api/template/columns");
  const data = await res.json();
  state.columns = data.columns || [];
  renderTableHeaders();
}

/**
 * Busca os processos já processados no banco
 */
async function fetchProcesses() {
  const res = await fetch("/api/processes");
  const data = await res.json();
  state.processes = data.processes || [];

  if (state.processes.length > 0 && !state.selectedProcessId) {
    selectProcess(state.processes[0].id);
  } else {
    renderTableBody();
  }
}

/**
 * Renderiza o cabeçalho da planilha corporativa
 */
function renderTableHeaders() {
  els.tableHeaders.innerHTML = "";
  state.columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col.label;
    els.tableHeaders.appendChild(th);
  });
}

/**
 * Renderiza as linhas da planilha com suporte a edição inline e badge de citação (fls./página)
 */
function renderTableBody() {
  els.tableBody.innerHTML = "";

  if (state.processes.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = state.columns.length || 1;
    td.style.textAlign = "center";
    td.style.color = "#64748b";
    td.textContent = "Nenhum processo na planilha. Faça o upload de um PDF acima.";
    tr.appendChild(td);
    els.tableBody.appendChild(tr);
    return;
  }

  state.processes.forEach((proc) => {
    const tr = document.createElement("tr");
    if (proc.id === state.selectedProcessId) {
      tr.classList.add("selected-row");
    }

    // Ao clicar na linha, ativa este processo para o Chatbot RAG
    tr.addEventListener("click", () => selectProcess(proc.id));

    state.columns.forEach((col) => {
      const td = document.createElement("td");
      const fieldKey = col.key;
      const fieldValue = (proc.fields && proc.fields[fieldKey]) || "—";
      const citationPage = proc.citations && proc.citations[fieldKey];

      // Div editável inline para correção rápida do analista
      const valueDiv = document.createElement("div");
      valueDiv.className = "cell-editable";
      valueDiv.contentEditable = "true";
      valueDiv.textContent = fieldValue;

      // Salva automaticamente no back-end ao sair da célula (evento blur)
      valueDiv.addEventListener("blur", async () => {
        const newValue = valueDiv.textContent.trim();
        if (newValue !== fieldValue) {
          await saveInlineEdit(proc.id, fieldKey, newValue);
        }
      });

      td.appendChild(valueDiv);

      // Se houver rastreabilidade de página (citation.py), exibe o badge
      if (citationPage) {
        const badge = document.createElement("span");
        badge.className = "cite-badge";
        badge.textContent = `📍 ${citationPage}`;
        td.appendChild(badge);
      }

      tr.appendChild(td);
    });

    els.tableBody.appendChild(tr);
  });
}

/**
 * Seleciona um processo para conversar no Chatbot RAG lateral
 */
function selectProcess(processId) {
  state.selectedProcessId = processId;
  const activeProc = state.processes.find((p) => p.id === processId);

  renderTableBody();

  if (activeProc) {
    const cnj = activeProc.fields?.numero_processo || activeProc.filename;
    els.activeProcessLabel.textContent = `Ativo: ${cnj}`;
    els.chatInput.disabled = false;
    els.btnSendChat.disabled = false;
  }
}

/**
 * Persiste edição inline de uma célula da planilha
 */
async function saveInlineEdit(processId, fieldKey, newValue) {
  await fetch(`/api/processes/${processId}/fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [fieldKey]: newValue }),
  });
}

/**
 * Configura os eventos de Upload, Exportação e Chat
 */
function bindEvents() {
  // Atualiza nome do arquivo selecionado
  els.pdfFileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) {
      els.fileLabelText.textContent = `📄 ${file.name}`;
    }
  });

  // Upload do PDF
  els.uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = els.pdfFileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("category", els.categorySelect.value);

    els.btnUpload.disabled = true;
    els.btnUpload.textContent = "Processando...";

    try {
      const res = await fetch("/api/processes/upload", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Falha ao processar o PDF.");

      const newRow = await res.json();
      state.processes.unshift(newRow);
      selectProcess(newRow.id);
      els.uploadForm.reset();
      els.fileLabelText.textContent = "📄 Selecionar ou arrastar PDF do processo...";
    } catch (err) {
      alert(err.message);
    } finally {
      els.btnUpload.disabled = false;
      els.btnUpload.textContent = "Processar PDF";
    }
  });

  // Exportação do Excel (.xlsx)
  els.btnExport.addEventListener("click", () => {
    window.location.href = "/api/processes/export/xlsx";
  });

  // Envio de pergunta ao Chatbot RAG
  els.chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = els.chatInput.value.trim();
    if (!question || !state.selectedProcessId) return;

    await sendChatQuestion(question);
  });

  // Chips de pergunta rápida (Ex: Bloqueio Sisbajud)
  els.quickChips.forEach((chip) => {
    chip.addEventListener("click", async () => {
      if (!state.selectedProcessId) {
        alert("Selecione um processo na tabela primeiro.");
        return;
      }
      const promptText = chip.getAttribute("data-prompt");
      await sendChatQuestion(promptText);
    });
  });
}

/**
 * Envia a pergunta para o endpoint RAG e renderiza a resposta com as páginas citadas
 */
async function sendChatQuestion(question) {
  appendMessage("user", question);
  els.chatInput.value = "";
  els.btnSendChat.disabled = true;

  try {
    const res = await fetch(`/api/processes/${state.selectedProcessId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    const reply = data.answer || "Sem resposta disponível.";
    appendMessage("assistant", reply);
  } catch (err) {
    appendMessage("system", "Erro ao consultar o motor RAG.");
  } finally {
    els.btnSendChat.disabled = false;
  }
}

function appendMessage(role, text) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  els.chatMessages.appendChild(div);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

document.addEventListener("DOMContentLoaded", initWorkspace);