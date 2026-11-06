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

### Módulo 3 — Central de XMLs

| Tela | Para quê |
|---|---|
| **Enviar XMLs** | Vários arquivos ou um **.zip** (até 300 MB): NF-e, NFC-e, CT-e, eventos de cancelamento e NFS-e (padrão nacional, ABRASF e Paulistana). Notas repetidas são ignoradas, canceladas são marcadas sozinhas, e o cliente é reconhecido pelo CNPJ do emitente ou do destinatário (filiais pela raiz) |
| **Resumo do mês** | Por cliente: quantidade e valor de notas emitidas e recebidas, canceladas e quem ficou sem nenhum XML |
| **Notas do cliente** | Emitidas e recebidas com filtros, totais de impostos, resumo por CFOP, **Excel** e **ZIP dos XMLs para importar no Domínio** |
| **Faturamento 12 meses** | Faturado mês a mês e **RBT12**, com alerta de sublimite e limite do Simples e do MEI (conferência, não apuração oficial) |
| **Notas sem cliente** | Notas cujo emitente e destinatário não estão cadastrados. Depois de cadastrar ou importar os clientes, um clique liga tudo |

Os XMLs ficam guardados compactados **dentro do banco**, então entram no backup diário.
A captura automática na SEFAZ e no Portal Nacional da NFS-e vem depois, quando houver certificado.

### Módulo 4 — Portal do cliente

| Quem | O que faz |
|---|---|
| **Cliente** (no celular ou computador) | Baixa guias, holerites, balanços e outros documentos; vê as guias a vencer; **envia** extratos, notas, fotos e planilhas (XMLs de notas vão direto para a Central de XMLs); vê os honorários em aberto com boleto/PIX e baixa os recibos dos pagos; confirma o recebimento dos protocolos; troca a própria senha. Quem é sócio de mais de uma empresa troca de empresa no menu |
| **Equipe** | **Portal → Publicar para cliente** (vários arquivos de uma vez); caixa de **documentos recebidos** com "processado"; **Acessos dos clientes**: cria login, gera senha provisória e manda pelo WhatsApp, desativa e vê a atividade (login, download, envio, com IP) |

Segurança: o login de cliente **só enxerga o /portal/ da própria empresa**. Não abre nenhuma tela interna, não vê outros clientes
e não baixa arquivos de outra empresa. Os arquivos não ficam públicos na internet: só saem por download com permissão.
Tipos aceitos: PDF, XML, ZIP, imagens, planilhas, Word, TXT, OFX, até 25 MB cada.

### Módulo 5 — Protocolos de entrega e recebimento

- Numeração sequencial por ano (2026/00001…), com itens, competência e quantidade.
- **Automático pelo portal:** publicar documentos gera um protocolo de entrega. Quando o cliente **baixa** ou clica em
  "confirmo o recebimento", a ciência fica registrada com data, hora, usuário e IP. Documentos enviados pelo cliente
  geram um protocolo de recebimento.
- **Manual (papel):** para entrega pessoal ou por motoboy, você cria o protocolo, imprime com as linhas de assinatura e depois marca como assinado.

### Módulo 6 — Horas e rentabilidade por cliente

- **Apontamento de horas:** cronômetro (iniciar/parar, que fica visível no topo enquanto roda) ou lançamento manual (`1:30`, `1h30`, `90`, `1,5h`).
  Ao marcar uma obrigação como entregue, dá para informar o tempo gasto e ele é lançado sozinho.
- **Custo da hora** de cada pessoa (menu do usuário → Custo da hora da equipe), com explicação de como calcular.
- **Relatório por período:** honorários × custo das horas por cliente, margem em R$ e %, R$ por hora, e **honorário mensal sugerido**
  para atingir a margem desejada. Vermelho = prejuízo, amarelo = abaixo da meta. Detalhe por atividade e por pessoa.

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

### Módulo 7 — Contábil

| Tela | Para quê |
|---|---|
| **Plano de contas** | Por cliente, criado a partir de um **plano padrão para PMEs** (1 Ativo, 2 Passivo/PL, 3 Receitas, 4 Custos e despesas), com natureza no padrão da ECD, grupo da DRE, "uso automático" (contas que as rotinas usam) e campo para o **código do plano referencial da RFB** |
| **Lançamentos** | Partidas dobradas com várias partidas, históricos padrão, conta pelo código reduzido, total de débito × crédito na tela. Numeração sequencial por cliente |
| **Extrato (OFX)** | Importa o extrato do banco sem duplicar e sugere a contrapartida por **regras** ("descrição contém TARIFA → despesas bancárias"). Você confere e contabiliza em lote; dá para criar regra na própria linha |
| **Notas fiscais** | Gera lançamentos a partir da Central de XMLs: vendas/serviços → D Clientes / C Receita; compras → D Estoque (ou Custo) / C Fornecedores. Só entram CFOPs de venda/compra (devoluções, remessas, transferências, ativo e uso e consumo ficam de fora) |
| **Relatórios** | Balancete (com níveis e Excel), Razão, Diário, DRE e Balanço Patrimonial, todos prontos para imprimir |
| **Fechamento** | Fecha o período (depois disso **nada é alterado nem excluído**: corrige-se por estorno), encerra o exercício (zera o resultado contra a ARE e transfere para lucros ou prejuízos acumulados) e permite reabertura excepcional (só administrador, com motivo) |
| **Auditoria** | Registro permanente de inclusões, alterações (com antes e depois), exclusões, estornos, fechamentos e reaberturas, com usuário, data/hora e IP |

Segue a **NBC ITG 2000** (escrituração em partidas dobradas, ordem cronológica, sem rasuras, correção por estorno).
Lançamentos não aparecem na administração avançada de propósito: toda mudança passa pelas regras e pela auditoria.

### Módulo 8 — Fiscal

| Tela | Para quê |
|---|---|
| **Receitas e perfil** | Receita bruta mensal por anexo (Simples) ou por atividade (Presumido), montada **a partir das notas** com um clique ou digitada. A folha de 12 meses alimenta o fator R |
| **Simples Nacional** | **RBT12**, alíquota efetiva pelas tabelas da **LC 123/2006 (redação da LC 155/2016)**, **fator R** (Anexo III ou V), **início de atividade** (RBT12 proporcional, Res. CGSN 140/2018), alertas de sublimite e limite, e memória de cálculo linha a linha |
| **Lucro Presumido** | PIS (0,65%) e COFINS (3%) cumulativos e ISS mensais; **IRPJ** (15% + adicional de 10% acima de R$ 60 mil/trimestre) e **CSLL** (9%) trimestrais, com presunção por atividade, outras receitas, retenções, meses de atividade e quotas |
| **Resumo por CFOP** | Saídas e entradas por CFOP com ICMS, ST, IPI, PIS/COFINS, ISS e **IBS/CBS** (Reforma Tributária: em 2026 o destaque é informativo) |
| **Tabelas usadas** | Mostra todas as tabelas e percentuais aplicados, para conferência |

Cada apuração é **salva com a memória de cálculo** e pode ser marcada como **conferida** (quem e quando).
É um cálculo de conferência: a apuração oficial continua no PGDAS-D (Simples) e na DCTFWeb/Sicalc (Presumido).

**Fica para a etapa 2:** geração dos arquivos ECD, ECF, EFD ICMS/IPI e EFD-Contribuições (validação no PVA),
partilha do DAS por tributo, segregação de ST/monofásicos no Simples, Lucro Real, apuração completa de ICMS (ST, DIFAL, CIAP).

## Avisos automáticos por e-mail

O sistema manda sozinho:

| Aviso | Para quem | Quando |
|---|---|---|
| **Documentos novos** | Pessoas com acesso ao portal daquela empresa que escolheram "a cada documento novo" | Na hora em que vocês publicam. **Um e-mail por envio**, mesmo com vários arquivos |
| **Resumo diário** | Quem escolheu "um resumo por dia" | Às 8h, com o que foi publicado nas últimas 24h e ainda não foi baixado |
| **Guia vencendo sem download** | Todos com aviso ligado (imediato ou resumo) | Às 8h, X dias antes do vencimento (padrão: 1), só se ninguém da empresa baixou |
| **Cliente enviou documentos** | Responsável pelo cliente no escritório (ou a equipe toda, se não houver) | Na hora |
| **Lembretes de honorários** | E-mail de cobrança do cliente | Às 8h (já existia; ligue em Configurações) |

- Cada pessoa escolhe **a cada documento / resumo diário / não quero**. O cliente escolhe no portal (Minha conta) e vocês, em **Portal → Acessos dos clientes**.
- Os arquivos **não vão anexados**: o e-mail traz o link do portal, por segurança, e assim o download continua valendo como ciência no protocolo.
- **Portal → Avisos por e-mail** mostra tudo que foi enviado, com erro e botão de reenviar, e tem o botão **e-mail de teste**. Avisos com erro são tentados de novo sozinhos por até 48h.
- Se o e-mail falhar, a publicação do documento **não** é afetada.
- Ligar/desligar e o prazo do lembrete de guia ficam em **⚙ Configurações**.

### Para ativar no servidor
1. Escolha a conta de envio: Google Workspace, Microsoft 365, Brevo ou Amazon SES. Prefira um endereço do seu domínio, por exemplo `avisos@seuescritorio.com.br`.
2. No painel do domínio, crie os registros **SPF** e **DKIM** que o provedor indicar (isso evita o spam).
3. No `.env`, tire o `#` da linha `EMAIL_BACKEND` e preencha `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
   `EMAIL_HOST_PASSWORD` e `DEFAULT_FROM_EMAIL`. Depois rode `docker compose up -d`.
4. Em **Portal → Avisos por e-mail**, clique em **Testar** e confira se chegou na caixa de entrada.

Enquanto o passo 3 não for feito, os avisos aparecem na lista como **"Não enviado"**: o sistema funciona normalmente, só não manda e-mail.
O WhatsApp automático (API oficial da Meta) pode ser ligado depois, aproveitando os mesmos avisos.

## Backups (proteção dos dados)
- O sistema faz **backup automático diário** do banco na pasta `backups/` do servidor e guarda 30 dias.
- **Recomendado:** copiar essa pasta para fora do servidor (Google Drive, Backblaze, outro computador).
  Ex.: com `rclone` configurado, agende no servidor (`crontab -e`):
  ```
  0 3 * * * rclone copy /root/honorarios/backups gdrive:backup-honorarios
  ```
- Os **arquivos do portal** também entram no backup diário (`backups/arquivos-AAAA-MM-DD_HHMM.tar.gz`).
- Para restaurar o banco: `sh scripts/restaurar.sh backups/honorarios-AAAA-MM-DD_HHMM.sql.gz`
- Para restaurar os arquivos: `sh scripts/restaurar_arquivos.sh backups/arquivos-AAAA-MM-DD_HHMM.tar.gz`

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
