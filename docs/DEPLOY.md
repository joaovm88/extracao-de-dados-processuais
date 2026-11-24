# Deploy de teste (acesso pelo navegador, sem abrir a IDE)

O sistema roda num servidor na nuvem, ligado 24 h. Você só abre o endereço no navegador (`https://…`), entra com usuário e senha e
usa normalmente: PDFs, planilhas e relatórios ficam guardados no servidor. Seu computador só precisa do navegador.

## Antes de começar: dados sigilosos

Os PDFs têm dados protegidos por **sigilo fiscal** e de partes. Antes de enviar processos reais a um servidor de terceiros,
confirme com a direção/TI se é permitido (de preferência usar a nuvem/servidor contratado pela instituição). Até lá, teste
com processos públicos ou com documentos sem dados sensíveis. Lembre que a IA (Gemini/Claude) já recebe o texto dos PDFs.

O deploy já vem com: login obrigatório (o sistema não sobe sem `APP_USERS`), HTTPS automático, cookies seguros e nenhuma
porta do sistema exposta direto (só o Caddy, nas portas 80/443).

## 1. Criar o servidor (uma vez)

Qualquer VM Linux com Docker serve. Tamanho recomendado: **2 vCPU e 4 GB de RAM**, 30 GB de disco (o OCR pesa).

- **Google Cloud** (Compute Engine, `e2-medium`, região `southamerica-east1`/São Paulo): mesma conta do Gemini; ~US$ 25–30/mês
  (os créditos de teste de novos usuários cobrem meses).
- **Oracle Cloud – Always Free** (VM Ampere A1, até 4 vCPU/24 GB): gratuito, região São Paulo; cadastro pede cartão.
- **Servidor da instituição**: o melhor para dados reais, se a TI liberar.

Na criação, libere as portas **80 e 443** (HTTP/HTTPS) no firewall. Anote o **IP externo** (ex.: `34.95.10.20`).

## 2. Instalar e subir (uma vez, ~10 min)

Entre no servidor por SSH (no Google Cloud há o botão "SSH" no próprio navegador) e rode:

```bash
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER && newgrp docker
git clone -b claude/great-cori-lknaw6 https://github.com/joaovm88/extracao-de-dados-processuais.git
cd extracao-de-dados-processuais
cp .env.example .env && nano .env
```

No `.env`, preencha pelo menos:

```
GEMINI_API_KEY=sua-chave
APP_USERS=joao:uma-senha-longa-aqui
SITE_DOMAIN=34-95-10-20.sslip.io      # o IP do servidor com hífens + .sslip.io (ou seu domínio)
```

Suba:

```bash
docker compose -f docker-compose.deploy.yml up -d --build
```

Abra `https://34-95-10-20.sslip.io` (o certificado HTTPS sai sozinho em 1–2 minutos na primeira vez).
O repositório é privado: no `git clone`, use um token do GitHub (Settings → Developer settings → Personal access tokens) como senha.

## 3. Rotina diária

- **Usar**: só abrir o endereço no navegador (pode salvar nos favoritos; funciona até no celular).
- **Atualizar para a última versão** (depois que eu subir correções):
  `./deploy/atualizar.sh` no servidor. Os casos e PDFs continuam lá.
- **Backup dos dados**: `./deploy/backup.sh` (gera `backups/data-AAAA-MM-DD.tgz`). Baixe de vez em quando.
- **Ver erros**: `docker compose -f docker-compose.deploy.yml logs app --tail 100`.
- **Trocar senha / incluir alguém**: edite `APP_USERS` no `.env` e rode `docker compose -f docker-compose.deploy.yml up -d`
  (trocar a senha encerra as sessões abertas).

## Por que esta versão é "leve"

O `docker-compose.yml` completo usa Redis + Elasticsearch (bom para muitos usuários, mas pede 4–6 GB só para eles). O deploy de
teste usa fila e busca em memória num único container: cabe numa VM pequena e é o mesmo modo do `run_local.py`. Se a direção
aprovar e o uso crescer, basta trocar para o compose completo no mesmo servidor.
