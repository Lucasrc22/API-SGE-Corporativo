# API SGE Corporativo

API REST para controle de estoque do setor de **Facilities**: café, material de
limpeza, descartáveis e papelaria consumidos pelos andares do escritório.

Controla o saldo do almoxarifado central, a distribuição para cada andar, o consumo,
as perdas e a divisão de itens entre setores — e avisa por e-mail quando um item chega
no limite de reposição.

## Stack

- **FastAPI** + **Pydantic** — rotas e validação
- **Uvicorn** — servidor ASGI
- **CSV** — persistência (sem banco de dados; os arquivos ficam em `app/data/`)
- **smtplib** — alerta de estoque baixo via Office 365

## Modelo de estoque

Cada produto tem quatro saldos independentes:

| Campo | Significado |
|---|---|
| `estoque_atual` | almoxarifado central |
| `estoque_4andar` | o que já subiu para o 4º andar |
| `estoque_5andar` | o que já subiu para o 5º andar |
| `desfalque` | perdas acumuladas (quebra, sumiço) |

O que entra nos andares ou no desfalque **sai** do estoque central:

| Operação | Efeito |
|---|---|
| `POST /products/entrada` | central `+n` — a compra chegando |
| `POST /products/retirada` | andar `+n`, central `−n` |
| `POST /products/consumo` | andar `−n`, central inalterado (a unidade já saiu na retirada) |
| `POST /products/desfalque` | desfalque `+n`, central `−n` |
| `PUT /products/{id}` | subir andar ou desfalque em `n` tira `n` do central; baixar devolve |

Toda operação grava uma linha em `movimentacoes.csv` com produto, tipo, quantidade,
local e timestamp — o histórico explica cada mudança de saldo.

## Endpoints

### Produtos

| Método | Rota | O que faz |
|---|---|---|
| `GET` | `/products` | lista todos os produtos |
| `POST` | `/products` | cadastra (desconta o desfalque inicial do central) |
| `PUT` | `/products/{id}` | atualiza; aplica a regra de saldo acima |
| `POST` | `/products/entrada` | entrada no estoque central |
| `POST` | `/products/retirada` | envia do central para o 4º ou 5º andar |
| `POST` | `/products/consumo` | baixa o consumido no andar |
| `POST` | `/products/desfalque` | registra perda |

### Histórico

| Método | Rota | O que faz |
|---|---|---|
| `GET` | `/movimentacoes` | histórico completo |
| `GET` | `/products/{id}/movimentacoes` | histórico de um produto |

### Setores

Controla itens distribuídos entre Financeiro, Fiscal, TI, Comercial, RH, DP,
Suprimentos e Jurídico.

| Método | Rota | O que faz |
|---|---|---|
| `GET` | `/setores` | lista os itens e a divisão por setor |
| `POST` | `/setores` | cadastra um item |
| `PUT` | `/setores/{id}` | atualiza a divisão; grava histórico campo a campo |
| `GET` | `/setores/historico` | entradas e saídas por setor |

Documentação interativa em `/docs` (Swagger) e `/redoc`.

## Alerta de estoque

Quando o estoque central de um produto marcado com `email_alerta_geral` fica igual ou
abaixo de `limite_alerta_geral`, a API dispara um e-mail para os destinatários de
`EMAIL_DESTINO` a cada movimentação — inclusive nas entradas.

## Configuração

Crie um `.env` na raiz do backend a partir do `.env.example`:

```
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_USER=usuario@dominio.com
EMAIL_PASSWORD=
EMAIL_FROM=usuario@dominio.com
EMAIL_DESTINO=["destino1@dominio.com", "destino2@dominio.com"]
```

`EMAIL_DESTINO` precisa ser um array JSON válido. Todas as chaves são obrigatórias — a
aplicação não sobe sem elas.

> `EMAIL_HOST` e `EMAIL_PORT` são exigidos pela configuração mas não são usados:
> `app/services/email_service.py` conecta em `smtp.office365.com:587` fixo.

**Nunca versione o `.env`.**

## Rodando

### Local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8007
```

### Docker

```bash
docker compose up -d --build
```

A API sobe na porta **8007**. O `docker-compose.yml` monta `app/data/` como volume —
os CSVs são o banco de dados e precisam viver no host, senão um rebuild zera o estoque.

## Estrutura

```
app/
├── main.py              FastAPI + CORS
├── core/config.py       variáveis de ambiente (pydantic-settings)
├── models/              schemas Pydantic
├── routers/
│   ├── products.py      produtos, movimentações e alertas
│   └── setores.py       distribuição por setor e histórico
├── services/
│   └── email_service.py envio do alerta de reposição
└── data/                os CSVs
```

## Concorrência

Os endpoints de escrita seguram um `RLock` durante todo o ciclo ler-alterar-gravar. Sem
isso, dois usuários simultâneos perdiam movimentações e podiam truncar o
`movimentacoes.csv` — uma leitura acontecia enquanto outra thread já tinha aberto o
arquivo em modo `w`. O envio de e-mail fica fora do lock para que uma chamada SMTP lenta
não segure as escritas.

## Limitações conhecidas

- Não há autenticação: quem alcança a API movimenta estoque, e as movimentações não
  registram quem fez.
- Não existe `DELETE` de produto nem de setor — só criar e editar.
- Persistência em CSV não escala e não tem transação: o `RLock` protege dentro de um
  processo, mas rodar mais de um worker do Uvicorn quebraria essa garantia.
- `movimentacoes.csv` tem codificação mista (latin-1 com resíduo de UTF-8), então
  registros antigos trazem o campo `andar` com acentuação corrompida.

## Cliente

O front-end em Streamlit que consome esta API fica em um repositório à parte.
