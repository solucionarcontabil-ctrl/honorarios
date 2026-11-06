# Sistema de Honorários — controle de cobrança do escritório

Sistema web (abre no navegador, de qualquer lugar) para controlar os honorários
dos clientes: cadastro, geração mensal, boleto/PIX, baixa, inadimplência e reajuste.

## O que ele faz

| Tela | Para quê |
|---|---|
| **Painel** | A receber no mês, recebido, em atraso, carteira mensal, gráfico de 12 meses e avisos (mês não gerado, reajustes pendentes) |
| **Clientes** | Cadastro com validação de CNPJ/CPF, regime, responsável (você ou seu pai), WhatsApp e e-mail, além do extrato completo do cliente |
| **Honorários (contratos)** | Valor mensal, dia de vencimento, índice de reajuste (IPCA, IGP-M, INPC, salário mínimo ou % fixo), mês do reajuste, 13º honorário e forma de pagamento. Um cliente pode ter vários (ex.: contábil + folha) |
| **Gerar mês** | Gera as mensalidades de todos os clientes de uma vez, com prévia. Inclui o 13º automaticamente e nunca duplica |
| **Cobranças** | Filtros (em aberto, vencidas, pagas), baixa manual, cancelamento, recibo para imprimir e exportação para Excel |
| **Boleto + PIX** | Pelo **Asaas**, com baixa automática quando o cliente paga. Ou **PIX na sua própria chave**, sem banco intermediário e sem taxa (a baixa é manual) |
| **Inadimplência** | Por cliente, com faixas de atraso (30/60/90+), valor atualizado com multa e juros e botão de cobrança pelo WhatsApp |
| **Reajustes** | Busca o índice acumulado de 12 meses no Banco Central. Você confere, ajusta e aplica, e fica um histórico de cada reajuste |
| **Lembretes por e-mail** | Automáticos antes e depois do vencimento (configurável) |

### Módulo 2 — Gestão do escritório

| Tela | Para quê |
|---|---|
| **Obrigações e tarefas** | Lista do mês com tudo que vence por cliente (DAS, DCTFWeb, EFD-Reinf, eSocial, FGTS Digital, SPED, ECD, ECF, DEFIS, IRPF…). Marca como entregue com nº do recibo, dispensa ("sem movimento"), passa para outra pessoa, filtra "só as minhas" |
| **Gerar obrigações do mês** | Cria as obrigações de todos os clientes conforme o regime, com o prazo já calculado em dias úteis |
| **Catálogo e prazos** | Lista de obrigações com regra de prazo (dia fixo, N-ésimo dia útil, último dia útil; antecipa/prorroga). Feriados nacionais automáticos e cadastro de feriados municipais (capital e litoral). **Os prazos vêm sugeridos — confira e ajuste.** |
| **Tarefas avulsas** | Pedidos entre você e seu pai ("pedir extrato", "ligar para cliente"), com prazo e responsável |
| **Certificados digitais** | Envie o .pfx (A1) e a senha: o sistema lê titular, CNPJ e validade e **não guarda o arquivo nem a senha**. A3/token cadastra à mão. Avisa 30 dias antes de vencer |
| **Importar clientes** | Planilha .xlsx ou .csv (ex.: relatório de empresas do Domínio Web). Reconhece as colunas pelo cabeçalho, não duplica e já vincula as obrigações do regime |

Tudo registra **quem fez** (você ou seu pai): quem criou, quem deu baixa e quem reajustou.

---

## Como colocar no ar (uma vez só)

### 1. Contratar um servidor (VPS)
Qualquer VPS Linux Ubuntu com **2 GB de RAM** basta (por volta de R$ 40–80/mês).
Opções com data center no Brasil: Hostinger, Locaweb, Magalu Cloud, AWS (região São Paulo).

### 2. Ter um domínio
Ex.: `honorarios.seuescritorio.com.br`. No painel do domínio (Registro.br etc.),
crie um registro **A** apontando para o IP do servidor.

### 3. Instalar (no servidor, via terminal SSH)
```bash
curl -fsSL https://get.docker.com | sh
```
```bash
git clone <endereço do seu repositório> honorarios && cd honorarios
```
```bash
cp .env.example .env && nano .env
```
No `.env`, preencha pelo menos: `DOMINIO`, `ALLOWED_HOSTS`, `SECRET_KEY` e `DB_PASSWORD`.
```bash
docker compose up -d --build
```

### 4. Criar os usuários (você e seu pai)
```bash
docker compose exec web python manage.py createsuperuser
```
Rode uma vez para cada pessoa. Depois acesse `https://seu-dominio` e entre.
Em **⚙ Configurações**, preencha o nome do escritório, a chave PIX, a multa e os juros.

---

## Boleto + PIX com baixa automática (Asaas)
1. Crie uma conta em asaas.com (há conta de testes "sandbox").
2. Copie a **chave de API** para `ASAAS_API_KEY` no `.env`. Use `ASAAS_AMBIENTE=producao` quando for valer.
3. Invente um token (uma senha qualquer, longa) e coloque em `ASAAS_WEBHOOK_TOKEN`.
4. No Asaas, em **Integrações → Webhooks**, cadastre:
   - URL: `https://seu-dominio/webhooks/asaas/`
   - Token de autenticação: o mesmo do passo 3
   - Eventos: cobranças (pagamento recebido/confirmado, removido, estornado)
5. Reinicie com `docker compose up -d`.

O Asaas cobra uma taxa por boleto ou PIX recebido (consulte a tabela atual). Se preferir não pagar
taxa, use só o **PIX na sua chave**: o sistema gera o QR Code e o copia-e-cola com o valor certo.

## Backups (proteção dos dados)
- O sistema faz **backup automático diário** do banco na pasta `backups/` do servidor e guarda 30 dias.
- **Recomendado:** copiar essa pasta para fora do servidor (Google Drive, Backblaze, outro computador).
  Ex.: com `rclone` configurado, agende no servidor (`crontab -e`):
  ```
  0 3 * * * rclone copy /root/honorarios/backups gdrive:backup-honorarios
  ```
- Para restaurar: `sh scripts/restaurar.sh backups/honorarios-AAAA-MM-DD_HHMM.sql.gz`

## Atualizar para uma nova versão
```bash
cd honorarios && git pull && docker compose up -d --build
```

## Rotina mensal sugerida
0. Começo do mês: **Escritório → Gerar obrigações do mês**. No dia a dia, use **Obrigações e tarefas → só as minhas**.
1. Dia 1: **Gerar mês**, conferir a prévia e gerar.
2. Em **Cobranças → Em aberto**, selecionar tudo e clicar em **Emitir boleto/PIX (Asaas)**.
   Ou abrir cada cobrança e usar **Gerar PIX na minha chave**.
3. Enviar pelo WhatsApp (botão pronto com a mensagem) ou deixar os lembretes por e-mail trabalharem.
4. Acompanhar a tela de **Inadimplência**.
5. No mês do reajuste, o painel avisa. Em **Reajustes**, confira os índices e aplique.

---

## Para desenvolvedores / testes locais
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```
```bash
python manage.py migrate && python manage.py createsuperuser
```
```bash
python manage.py dados_exemplo   # clientes fictícios para testar
```
```bash
python manage.py runserver
```
Testes automáticos: `python manage.py test cobranca`
