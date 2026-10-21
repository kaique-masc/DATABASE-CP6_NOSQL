# TicketFlash — Checkpoint 3 (MongoDB + Redis com Python)

FIAP — Mastering Relational and Non-Relational Database — Turma 2TDSPG — Prof. Vergílio Santos

## Integrantes

| RM | Nome |
|----|------|
| RM563982 | Felipe Augusto Lopes Ferreira |
| RM565802 | Kaique Mascarenhas dos Santos |

## Como executar

Pré-requisito: Docker Desktop.

```bash
docker compose up --build          # sobe api, mongo e redis
docker compose exec api python seed.py        # popula o MongoDB (12 eventos, 24 usuários, 60 pedidos)
docker compose exec api python subscriber.py  # (bônus) assinante Pub/Sub, em outro terminal
```

API em `http://localhost:8000` · documentação interativa em `http://localhost:8000/docs`.
Os índices são criados automaticamente na inicialização da API.

## Arquitetura

- **MongoDB** (fonte da verdade): `eventos`, `usuarios`, `pedidos`.
- **Redis** (rápido e temporário): cache de evento, reservas, ranking, limite de requisições e Pub/Sub.

## Endpoints

| Método | Rota | Descrição | Req. |
|--------|------|-----------|------|
| POST | `/eventos` | Cria evento | 2 |
| GET | `/eventos?categoria=&cidade=` | Lista com filtros aplicados no banco | 2 |
| GET | `/eventos/{id}` | Busca (cache-aside, retorna `origem`: `cache`/`banco`) e incrementa ranking | 2, 5, 7 |
| PUT | `/eventos/{id}` | Atualiza e invalida `evento:{id}` | 2, 9 |
| DELETE | `/eventos/{id}` | Remove e invalida `evento:{id}` | 2, 9 |
| GET | `/eventos/ranking` | Top 5 mais vistos (Sorted Set) | 7 |
| GET | `/relatorios/faturamento` | Faturamento por evento (`$group` + `$lookup`) | 4 |
| GET | `/relatorios/ocupacao/{eventoId}` | % vendido por setor (`$unwind` + `$project`) | 4 |
| POST | `/reservas` | Limite 10/min (429), checa vagas, cria reserva `SET NX EX 600` | 6, 8, 10 |
| POST | `/pedidos/confirmar` | Confirma (410 se a reserva expirou), `$inc` atômico, grava pedido | 10 |
| POST/GET | `/usuarios` | Apoio para testes | — |

Coleção de requisições: `requests.http`.

## Req. 1 — Justificativa da modelagem (embutir × referenciar)

| Relação | Decisão | Justificativa |
|---------|---------|---------------|
| Evento → `local` | **Embutido** (objeto) | É lido sempre junto com o evento e não é compartilhado nem consultado isoladamente. |
| Evento → `atracoes` | **Embutido** (array de strings) | Lista pequena, limitada e sem vida própria fora do evento. |
| Evento → `setores` | **Embutido** (array de subdocumentos) | Os setores só existem dentro do evento; a página do evento os exibe juntos (uma leitura só). Também permite o `$inc` atômico de `vendidos` no mesmo documento do evento, sem transação, e a estrutura varia por evento. |
| Pedido → Usuário | **Referenciado** (`usuarioId`) | Um usuário tem muitos pedidos (cresce sem limite); embutir inflaria o documento do usuário e duplicaria dados. |
| Pedido → Evento | **Referenciado** (`eventoId`) | Um evento pode ter milhares de pedidos, o que estouraria o limite de 16 MB e reescreveria o evento a cada compra. Pedidos também são consultados isoladamente (relatórios). O nome do evento vem por `$lookup`. |

Os pedidos guardam `setor` (nome) e `valorTotal` no momento da compra, preservando o histórico caso o preço do evento mude.

## Req. 3 — Índices e `explain()`

Criados em `app/database.py`:

| Coleção | Índice | Tipo |
|---------|--------|------|
| `usuarios` | `email` | **único** |
| `eventos` | `categoria + data` | **composto** |
| `pedidos` | `usuarioId` | simples |
| `pedidos` | `eventoId + status` | composto (apoia o relatório de faturamento) |
| `eventos` | `local.cidade` | simples (apoia o filtro por cidade) |

Comando usado (executar após o seed). Versão para PowerShell (Windows):

```powershell
docker compose exec mongo mongosh ticketflash --eval "printjson(db.eventos.find({categoria: 'show'}).sort({data: 1}).explain('executionStats').queryPlanner.winningPlan)"
```

Versão para Linux/Mac:

```bash
docker compose exec mongo mongosh ticketflash --eval 'printjson(db.eventos.find({categoria: "show"}).sort({data: 1}).explain("executionStats").queryPlanner.winningPlan)'
```

Saída obtida:

```text
{
  stage: 'FETCH',
  inputStage: {
    stage: 'IXSCAN',
    keyPattern: { categoria: 1, data: 1 },
    indexName: 'idx_categoria_data',
    isMultiKey: false,
    isUnique: false,
    indexVersion: 2,
    direction: 'forward',
    indexBounds: {
      categoria: [ '["show", "show"]' ],
      data: [ '[MinKey, MaxKey]' ]
    }
  }
}
```

O plano vencedor usa **IXSCAN** no índice composto `idx_categoria_data` (categoria + data), seguido de **FETCH** para buscar os documentos. Não há **COLLSCAN**, ou seja, o MongoDB não percorreu a coleção inteira. O filtro por `categoria` usou o intervalo `["show","show"]` do índice, e a ordenação por `data` saiu direto dele, sem etapa de SORT em memória.

## Decisões de implementação

- **Cache-aside (Req. 5 e 9):** `GET /eventos/{id}` consulta `evento:{id}` no Redis (TTL 60 s); se não existir, lê do MongoDB e repopula. Update e delete apagam a chave. Ao confirmar um pedido o cache também é invalidado, pois `vendidos` mudou.
- **Reserva (Req. 6):** chave `reserva:{eventoId}:{setor}:{usuarioId}` com `SET NX EX 600`; só uma reserva ativa por usuário/setor (409 se repetir).
- **Ranking (Req. 7):** `ZINCRBY ranking:eventos` a cada consulta (cache ou banco); top 5 com `ZREVRANGE`.
- **Limite (Req. 8):** `INCR limite:reservas:{usuarioId}` + `EXPIRE 60`; acima de 10 → 429 com `Retry-After`.
- **Compra (Req. 10):** a reserva é lida e apagada de forma atômica (`GETDEL`), evitando confirmação dupla. O incremento usa `update_one` com a condição de capacidade no próprio filtro (`$elemMatch` com `vendidos <= capacidade - quantidade`), então nunca ultrapassa a capacidade. O incremento é feito antes de gravar o pedido, para nunca deixar pedido sem vaga garantida. Reserva inexistente/expirada → 410 e nada é gravado.
- **Bônus Pub/Sub:** cada pedido confirmado publica JSON em `pedidos:confirmados`; `subscriber.py` imprime no console.
- O campo "atrações" do enunciado está como `atracoes` (sem acento) no código.

## Estrutura do projeto

```text
docker-compose.yml   Dockerfile   requirements.txt
seed.py              subscriber.py   requests.http
app/database.py      app/main.py
```