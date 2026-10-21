"""Conexões com MongoDB e Redis e criação dos índices."""
import os

import redis
from pymongo import ASCENDING, MongoClient

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "ticketflash")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

mongo_client = MongoClient(MONGO_URL)
db = mongo_client[MONGO_DB]
eventos = db["eventos"]
usuarios = db["usuarios"]
pedidos = db["pedidos"]

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def criar_indices() -> None:
    """Req. 3 - índices criados pelo código (idempotente)."""
    usuarios.create_index([("email", ASCENDING)], unique=True, name="uniq_email")
    eventos.create_index(
        [("categoria", ASCENDING), ("data", ASCENDING)], name="idx_categoria_data"
    )
    pedidos.create_index([("usuarioId", ASCENDING)], name="idx_usuarioId")
    # apoio aos relatórios e filtros
    pedidos.create_index([("eventoId", ASCENDING), ("status", ASCENDING)], name="idx_evento_status")
    eventos.create_index([("local.cidade", ASCENDING)], name="idx_cidade")
