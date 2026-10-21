"""TicketFlash - API REST (FastAPI) com MongoDB (fonte da verdade) e Redis (cache/temporário)."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .database import criar_indices, eventos, pedidos, r, usuarios

CACHE_TTL = 60          # Req. 5  - cache de evento (s)
RESERVA_TTL = 600       # Req. 6  - reserva temporária (10 min)
LIMITE_RESERVAS = 10    # Req. 8  - reservas por usuário por minuto
JANELA_LIMITE = 60
RANKING_KEY = "ranking:eventos"
CANAL_PEDIDOS = "pedidos:confirmados"


@asynccontextmanager
async def lifespan(app: FastAPI):
    criar_indices()
    yield


app = FastAPI(title="TicketFlash API", lifespan=lifespan)


# ---------------------------------------------------------------- helpers
def oid(valor: str) -> ObjectId:
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Id inválido ou não encontrado")


def ser(obj):
    """Converte ObjectId/datetime recursivamente para tipos serializáveis em JSON."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: ser(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ser(v) for v in obj]
    return obj


def chave_evento(evento_id: str) -> str:
    return f"evento:{evento_id}"


def chave_reserva(evento_id: str, setor: str, usuario_id: str) -> str:
    return f"reserva:{evento_id}:{setor}:{usuario_id}"


# ---------------------------------------------------------------- modelos
class Local(BaseModel):
    cidade: str
    endereco: str


class SetorIn(BaseModel):
    nome: str
    preco: float = Field(gt=0)
    capacidade: int = Field(gt=0)
    vendidos: int = Field(default=0, ge=0)


class EventoIn(BaseModel):
    nome: str
    categoria: str
    data: datetime
    local: Local
    atracoes: list[str] = []
    setores: list[SetorIn]


class EventoUpdate(BaseModel):
    nome: Optional[str] = None
    categoria: Optional[str] = None
    data: Optional[datetime] = None
    local: Optional[Local] = None
    atracoes: Optional[list[str]] = None
    setores: Optional[list[SetorIn]] = None


class UsuarioIn(BaseModel):
    nome: str
    email: str
    cidade: str


class ReservaIn(BaseModel):
    usuarioId: str
    eventoId: str
    setor: str
    quantidade: int = Field(gt=0, le=10)


class ConfirmarIn(BaseModel):
    usuarioId: str
    eventoId: str
    setor: str


# ---------------------------------------------------------------- usuários (apoio)
@app.post("/usuarios", status_code=201)
def criar_usuario(u: UsuarioIn):
    doc = u.model_dump()
    doc["dataCadastro"] = datetime.now(timezone.utc)
    try:
        res = usuarios.insert_one(doc)
    except Exception as exc:  # DuplicateKeyError no índice único de email
        if "duplicate" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409, detail="E-mail já cadastrado")
        raise
    return {"_id": str(res.inserted_id)}


@app.get("/usuarios")
def listar_usuarios(limit: int = Query(20, le=100)):
    return [ser(u) for u in usuarios.find().limit(limit)]


# ---------------------------------------------------------------- CRUD de eventos (Req. 2)
@app.post("/eventos", status_code=201)
def criar_evento(e: EventoIn):
    res = eventos.insert_one(e.model_dump())
    return {"_id": str(res.inserted_id)}


@app.get("/eventos")
def listar_eventos(
    categoria: Optional[str] = None,
    cidade: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    filtro: dict = {}
    if categoria:
        filtro["categoria"] = categoria
    if cidade:
        filtro["local.cidade"] = cidade
    # o filtro é aplicado na consulta ao banco (usa o índice composto/cidade)
    return [ser(e) for e in eventos.find(filtro).sort("data", 1).limit(limit)]


# Req. 7 - precisa vir ANTES de /eventos/{evento_id}
@app.get("/eventos/ranking")
def ranking_eventos():
    top = r.zrevrange(RANKING_KEY, 0, 4, withscores=True)
    ids = [oid(i) for i, _ in top]
    nomes = {str(e["_id"]): e["nome"] for e in eventos.find({"_id": {"$in": ids}}, {"nome": 1})}
    return [
        {"posicao": pos, "eventoId": i, "nome": nomes.get(i), "visualizacoes": int(score)}
        for pos, (i, score) in enumerate(top, start=1)
    ]


@app.get("/eventos/{evento_id}")
def buscar_evento(evento_id: str):
    _id = oid(evento_id)
    chave = chave_evento(evento_id)

    cached = r.get(chave)
    if cached:
        r.zincrby(RANKING_KEY, 1, evento_id)
        return {"origem": "cache", "ttl": r.ttl(chave), "evento": json.loads(cached)}

    doc = eventos.find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    doc = ser(doc)
    r.set(chave, json.dumps(doc), ex=CACHE_TTL)
    r.zincrby(RANKING_KEY, 1, evento_id)
    return {"origem": "banco", "ttl": CACHE_TTL, "evento": doc}


@app.put("/eventos/{evento_id}")
def atualizar_evento(evento_id: str, dados: EventoUpdate):
    _id = oid(evento_id)
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
    res = eventos.update_one({"_id": _id}, {"$set": campos})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    r.delete(chave_evento(evento_id))  # Req. 9 - invalidação no update
    return {"atualizado": True, "cacheInvalidado": True}


@app.delete("/eventos/{evento_id}")
def remover_evento(evento_id: str):
    _id = oid(evento_id)
    res = eventos.delete_one({"_id": _id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    r.delete(chave_evento(evento_id))  # Req. 9 - invalidação no delete
    r.zrem(RANKING_KEY, evento_id)
    return {"removido": True, "cacheInvalidado": True}


# ---------------------------------------------------------------- relatórios (Req. 4)
@app.get("/relatorios/faturamento")
def faturamento_por_evento():
    pipeline = [
        {"$match": {"status": "confirmado"}},
        {"$group": {"_id": "$eventoId", "faturamento": {"$sum": "$valorTotal"}, "pedidos": {"$sum": 1}}},
        {"$lookup": {"from": "eventos", "localField": "_id", "foreignField": "_id", "as": "evento"}},
        {"$unwind": "$evento"},
        {"$project": {"_id": 0, "eventoId": "$_id", "nome": "$evento.nome", "faturamento": 1, "pedidos": 1}},
        {"$sort": {"faturamento": -1}},
    ]
    return [ser(x) for x in pedidos.aggregate(pipeline)]


@app.get("/relatorios/ocupacao/{evento_id}")
def ocupacao_por_setor(evento_id: str):
    pipeline = [
        {"$match": {"_id": oid(evento_id)}},
        {"$unwind": "$setores"},
        {
            "$project": {
                "_id": 0,
                "evento": "$nome",
                "setor": "$setores.nome",
                "capacidade": "$setores.capacidade",
                "vendidos": "$setores.vendidos",
                "percentualVendido": {
                    "$round": [
                        {
                            "$multiply": [
                                {"$divide": ["$setores.vendidos", "$setores.capacidade"]},
                                100,
                            ]
                        },
                        2,
                    ]
                },
            }
        },
        {"$sort": {"percentualVendido": -1}},
    ]
    resultado = list(eventos.aggregate(pipeline))
    if not resultado:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return resultado


# ---------------------------------------------------------------- fluxo de compra (Req. 6, 8, 10)
def checar_limite(usuario_id: str) -> None:
    """Req. 8 - INCR + EXPIRE: máx. 10 reservas por usuário por minuto."""
    chave = f"limite:reservas:{usuario_id}"
    contagem = r.incr(chave)
    if contagem == 1 or r.ttl(chave) == -1:
        r.expire(chave, JANELA_LIMITE)
    if contagem > LIMITE_RESERVAS:
        raise HTTPException(
            status_code=429,
            detail="Limite de 10 reservas por minuto excedido",
            headers={"Retry-After": str(max(r.ttl(chave), 1))},
        )


@app.post("/reservas", status_code=201)
def criar_reserva(rv: ReservaIn):
    checar_limite(rv.usuarioId)

    if not usuarios.find_one({"_id": oid(rv.usuarioId)}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    evento = eventos.find_one(
        {"_id": oid(rv.eventoId), "setores.nome": rv.setor}, {"setores.$": 1}
    )
    if not evento:
        raise HTTPException(status_code=404, detail="Evento ou setor não encontrado")
    setor = evento["setores"][0]
    if setor["capacidade"] - setor["vendidos"] < rv.quantidade:
        raise HTTPException(status_code=409, detail="Setor sem vagas suficientes")

    chave = chave_reserva(rv.eventoId, rv.setor, rv.usuarioId)
    valor = json.dumps(
        {"quantidade": rv.quantidade, "preco": setor["preco"], "criadaEm": datetime.now(timezone.utc).isoformat()}
    )
    # Req. 6 - SET NX EX: só cria se não existir; expira sozinha em 10 min
    if not r.set(chave, valor, nx=True, ex=RESERVA_TTL):
        raise HTTPException(status_code=409, detail="Já existe uma reserva ativa para este setor")
    return {"reserva": chave, "expiraEmSegundos": r.ttl(chave), "quantidade": rv.quantidade}


@app.post("/pedidos/confirmar", status_code=201)
def confirmar_pedido(c: ConfirmarIn):
    chave = chave_reserva(c.eventoId, c.setor, c.usuarioId)
    # GETDEL: lê e apaga atomicamente (evita confirmação dupla da mesma reserva)
    bruto = r.getdel(chave)
    if bruto is None:
        raise HTTPException(status_code=410, detail="Reserva expirada ou inexistente")
    reserva = json.loads(bruto)
    qtd = reserva["quantidade"]

    evento = eventos.find_one(
        {"_id": oid(c.eventoId), "setores.nome": c.setor}, {"setores.$": 1}
    )
    if not evento:
        raise HTTPException(status_code=404, detail="Evento ou setor não encontrado")
    setor = evento["setores"][0]

    # $inc atômico e condicional: a condição está no próprio filtro do update_one
    res = eventos.update_one(
        {
            "_id": oid(c.eventoId),
            "setores": {
                "$elemMatch": {
                    "nome": c.setor,
                    "vendidos": {"$lte": setor["capacidade"] - qtd},
                }
            },
        },
        {"$inc": {"setores.$.vendidos": qtd}},
    )
    if res.modified_count == 0:
        raise HTTPException(status_code=409, detail="Capacidade do setor esgotada")

    pedido = {
        "usuarioId": oid(c.usuarioId),
        "eventoId": oid(c.eventoId),
        "setor": c.setor,
        "quantidade": qtd,
        "valorTotal": round(qtd * setor["preco"], 2),
        "status": "confirmado",
        "criadoEm": datetime.now(timezone.utc),
    }
    pedido_id = pedidos.insert_one(pedido).inserted_id

    r.delete(chave_evento(c.eventoId))  # vendidos mudou: evita cache desatualizado
    # Bônus - Pub/Sub
    r.publish(
        CANAL_PEDIDOS,
        json.dumps(
            {"pedidoId": str(pedido_id), "eventoId": c.eventoId, "usuarioId": c.usuarioId,
             "setor": c.setor, "quantidade": qtd, "valorTotal": pedido["valorTotal"]}
        ),
    )
    return {"pedidoId": str(pedido_id), "status": "confirmado", "valorTotal": pedido["valorTotal"]}
