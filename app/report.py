"""Relatório em PDF das medições (tokens, tempos, cortes, registro de execução), gerado no servidor com PyMuPDF (sem dependência nova)."""
import html
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

import pymupdf

COR = {"ok": "#168821", "blue": "#1351b4", "warn": "#b45309", "err": "#c42d12", "muted": "#8a97a8", "line": "#e3e8ef"}


def fmt_t(x: Optional[float]) -> str:
    if x is None:
        return "-"
    x = round(x)
    return f"{x} s" if x < 60 else f"{x // 60} min {x % 60} s"


def fmt_n(n: Optional[float]) -> str:
    if n is None:
        return "-"
    if n >= 1e6:
        return f"{n / 1e6:.2f}".replace(".", ",") + (" milhões" if n >= 2e6 else " milhão")
    if n >= 1000:
        return f"{n / 1000:.1f}".replace(".", ",") + " mil"
    return str(int(n))


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def agregar(linhas: list[dict], documentos: list[dict]) -> dict:
    """Os mesmos números da tela Medições."""
    ls = [l for l in linhas if l.get("medidas") or l.get("docs")]
    com = [l for l in ls if l.get("medidas")]
    cortados = [l for l in com if l["medidas"]["contexto"].get("cortado")]
    tempos = [d["tempos"] for l in ls for d in l.get("docs", []) if d.get("tempos")]
    soma = lambda it: sum(x or 0 for x in it)
    tin = soma(l["medidas"].get("tokens", {}) and l["medidas"]["tokens"].get("entrada") for l in com)
    tout = soma(((l["medidas"].get("tokens") or {}).get("saida") or 0) + ((l["medidas"].get("tokens") or {}).get("raciocinio") or 0) for l in com)
    ia_s = soma(l["medidas"].get("extracao_s") for l in com)
    le_s = soma(t.get("total_s") for t in tempos)
    med_ia = ia_s / len(com) if com else 0
    por_dia = int(8 * 3600 // (med_ia + (le_s / len(tempos) if tempos else 0))) if med_ia else None
    st = lambda s: sum(1 for d in documentos if d["status"] == s)
    manuais = sum(1 for l in ls if l.get("estado") == "manual")
    return {"ls": ls, "com": com, "cortados": cortados, "tin": tin, "tout": tout, "ia_s": ia_s, "le_s": le_s, "med_ia": med_ia,
            "por_dia": por_dia, "ocr_p": soma(t.get("ocr_paginas") for t in tempos), "ocr_s": soma(t.get("ocr_s") for t in tempos),
            "st": {k: st(k) for k in ("done", "processing", "queued", "error")}, "n_docs": len(documentos), "manuais": manuais,
            "sem_ext": max(len(ls) - len(com) - manuais, 0), "todos": len(linhas)}


def _barra(partes: list[tuple[float, str, str]], mostrar: bool = True) -> str:
    """Barra empilhada + legenda. partes: (valor, cor, rótulo). `mostrar`: escreve o valor na legenda (contagens)."""
    total = sum(v for v, _, _ in partes) or 1
    cel = "".join(f'<td style="width:{max(v / total * 100, 1.5):.1f}%;background-color:{COR[c]};font-size:6pt">&nbsp;</td>' for v, c, _ in partes if v > 0)
    barra = f'<table style="width:100%" cellspacing="0" cellpadding="0"><tr>{cel or f"<td style=\"background-color:#e3e8ef;font-size:6pt\">&nbsp;</td>"}</tr></table>'
    leg = " &nbsp; ".join(f'<span style="color:{COR[c]}">&#9632;</span> {_e(r)}' + (f" <b>{_e(int(v))}</b>" if mostrar else "") for v, c, r in partes)
    return barra + f'<p style="font-size:8pt;color:#5d6b7c;margin-top:3px">{leg}</p>'


def _quando(ts: Optional[float], tz: timezone) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d/%m/%Y %H:%M:%S") if ts else "-"


def montar_html(nome: str, linhas: list[dict], documentos: list[dict], tz: timezone) -> str:
    a = agregar(linhas, documentos)
    agora = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    kpi = lambda t, v, s: f'<td style="width:25%;background-color:#f3f5f9" valign="top"><span style="font-size:8pt;color:#5d6b7c">{t}</span><br><b style="font-size:15pt">{v}</b><br><span style="font-size:7.5pt;color:#5d6b7c">{s}</span></td>'
    por_dia = f"~{a['por_dia']} /8 h" if a["por_dia"] else "-"
    h = [f'<h1 style="font-size:17pt;color:#0f1b2d">Relatório de medições</h1>'
         f'<p style="color:#5d6b7c;font-size:9.5pt">Devedor/grupo: <b>{_e(nome)}</b> &nbsp;|&nbsp; gerado em {agora}</p>',
         '<table style="width:100%" cellspacing="6" cellpadding="4"><tr>'
         + kpi("Processos extraídos", f"{len(a['com'])} de {a['todos']}", f"{len(a['ls'])} com PDF enviado")
         + kpi("Tokens gastos", fmt_n(a["tin"] + a["tout"]), f"{fmt_n(a['tin'])} entrada / {fmt_n(a['tout'])} saída")
         + kpi("Tempo médio por processo", fmt_t(a["med_ia"]) if a["com"] else "-", "só a chamada à IA")
         + kpi("Ritmo estimado", por_dia, "um processo por vez")
         + "</tr></table>",
         '<h2 style="font-size:11pt;color:#1351b4;margin-top:10px">Visão geral</h2>',
         '<table style="width:100%" cellspacing="8" cellpadding="0"><tr><td style="width:50%" valign="top">'
         f'<b>PDFs ({a["n_docs"]})</b>' + _barra([(a["st"]["done"], "ok", "lidos"), (a["st"]["processing"], "blue", "lendo"),
                                                  (a["st"]["queued"], "muted", "na fila"), (a["st"]["error"], "err", "com erro")])
         + '</td><td style="width:50%" valign="top">'
         f'<b>Processos com PDF ({len(a["ls"])})</b>' + _barra([(len(a["com"]) - len(a["cortados"]), "ok", "IA viu tudo"), (len(a["cortados"]), "warn", "cortados"),
                                                               (a["manuais"], "blue", "feitos manualmente"), (a["sem_ext"], "muted", "sem extração")])
         + '</td></tr><tr><td valign="top">'
         f'<b>Tempo total ({fmt_t(a["le_s"] + a["ia_s"])})</b>' + _barra([(round(a["le_s"]), "blue", f"leitura dos PDFs ({fmt_t(a['le_s'])})"),
                                                                           (round(a["ia_s"]), "warn", f"IA ({fmt_t(a['ia_s'])})")], mostrar=False)
         + f'<p style="font-size:8pt;color:#5d6b7c">OCR: {a["ocr_p"]} página(s) em {fmt_t(a["ocr_s"])}</p>'
         '</td><td valign="top">'
         f'<b>Tokens ({fmt_n(a["tin"] + a["tout"])})</b>' + _barra([(a["tin"], "blue", f"entrada ({fmt_n(a['tin'])})"), (a["tout"], "warn", f"saída ({fmt_n(a['tout'])})")], mostrar=False)
         + "</td></tr></table>"]
    # por processo
    th = lambda larg, *c: "<tr>" + "".join(f'<td style="width:{w}%;background-color:#eef2f7"><b style="font-size:7.5pt">{x}</b></td>' for w, x in zip(larg, c)) + "</tr>"
    lin = []
    for l in a["ls"]:
        m, c = l.get("medidas") or {}, (l.get("medidas") or {}).get("contexto")
        tk = m.get("tokens") or {}
        d = [x["tempos"] for x in l.get("docs", []) if x.get("tempos")]
        env = "-" if not c else f"{c['paginas_enviadas']} de {c['paginas_total']} págs." + (" <b style='color:#b45309'>(cortado)</b>" if c.get("cortado") else "")
        lin.append("<tr>" + "".join(f'<td style="font-size:7.5pt">{x}</td>' for x in (
            _e(l["numero"]), c["paginas_total"] if c else (l.get("paginas") or "-"), env,
            f"{fmt_n(tk.get('entrada'))} / {fmt_n((tk.get('saida') or 0) + (tk.get('raciocinio') or 0))}" if tk else "-",
            fmt_t(m.get("extracao_s")) if m else "-", fmt_t(sum(x.get("total_s") or 0 for x in d)) if d else "-")) + "</tr>")
    h.append('<h2 style="font-size:11pt;color:#1351b4;margin-top:10px">Por processo</h2><table style="width:100%" cellspacing="0" cellpadding="3">'
             + th((23, 8, 27, 20, 11, 11), "Processo", "Págs.", "Enviadas à IA", "Tokens (entrada / saída)", "Tempo da IA", "Leitura dos PDFs") + "".join(lin or ['<tr><td colspan="6">Nenhum processo com PDF.</td></tr>']) + "</table>")
    # registro de execução
    reg = []
    for d in documentos:
        if d.get("started_at"):
            res = "erro" if d["status"] == "error" else "êxito" if d["status"] == "done" else "em andamento"
            reg.append((d["started_at"], "Leitura do PDF", d["filename"], d["started_at"], d.get("finished_at"), res, d.get("error") if d["status"] == "error" else ""))
    for l in linhas:
        e = l.get("ext") or {}
        if e.get("inicio"):
            res = "erro" if e.get("status") == "erro" else "êxito" if e.get("status") == "ok" else "em andamento"
            reg.append((e["inicio"], "Extração por IA", l["numero"], e["inicio"], e.get("fim"), res, l.get("error") if res == "erro" else ""))
    reg.sort(key=lambda r: -r[0])
    cor = {"êxito": COR["ok"], "erro": COR["err"], "em andamento": COR["blue"]}
    rl = "".join("<tr>" + "".join(f'<td style="font-size:7.5pt">{x}</td>' for x in (
        r[1], _e(r[2]), _quando(r[3], tz), _quando(r[4], tz), fmt_t(r[4] - r[3]) if r[4] else "-",
        f'<b style="color:{cor[r[5]]}">{r[5]}</b>' + (f'<br><span style="color:#5d6b7c">{_e(str(r[6])[:110])}</span>' if r[6] else ""))) + "</tr>" for r in reg)
    h.append('<h2 style="font-size:11pt;color:#1351b4;margin-top:10px">Registro de execução (data, hora e segundos)</h2><table style="width:100%" cellspacing="0" cellpadding="3">'
             + th((14, 25, 16, 16, 8, 21), "Etapa", "Arquivo / processo", "Início", "Fim", "Duração", "Resultado") + (rl or '<tr><td colspan="6">Ainda não há registros.</td></tr>') + "</table>")
    h.append('<h2 style="font-size:11pt;color:#1351b4;margin-top:10px">Como ler</h2><p style="font-size:8pt;color:#5d6b7c">'
             "<b>Tokens:</b> pedaços de texto que a IA lê (entrada) e escreve (saída, inclui o raciocínio); cerca de 2,5 caracteres por token em português. "
             "<b>Tempo médio por processo:</b> só a chamada à IA, sem a leitura/OCR dos PDFs; esperas por erro 503 ou limite de uso entram na conta. "
             "<b>Ritmo estimado:</b> 8 h divididas pelo tempo médio (IA + leitura do PDF), um processo por vez; é uma projeção que muda com o modelo e o tamanho dos processos. "
             "<b>Cortado:</b> o processo passou do limite de texto enviado à IA e parte das páginas ficou de fora.</p>")
    return "".join(h)


def gerar_pdf(nome: str, linhas: list[dict], documentos: list[dict], tz_min: int = 180) -> bytes:
    """tz_min: minutos de diferença para o UTC como o navegador informa (Brasília = 180, ou seja, UTC-3)."""
    tz = timezone(timedelta(minutes=-tz_min))
    story = pymupdf.Story(html=montar_html(nome, linhas, documentos, tz), em=10)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    folha = pymupdf.paper_rect("a4")
    area = folha + (36, 36, -36, -40)
    mais = 1
    while mais:
        dev = writer.begin_page(folha)
        mais, _ = story.place(area)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()
