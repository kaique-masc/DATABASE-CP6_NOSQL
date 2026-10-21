"""Assinante Pub/Sub (bônus): registra no console cada pedido confirmado.

Uso:  docker compose exec api python subscriber.py
"""
import os

import redis

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
)

pubsub = r.pubsub()
pubsub.subscribe("pedidos:confirmados")
print("Aguardando mensagens no canal 'pedidos:confirmados' (Ctrl+C para sair)...", flush=True)

for msg in pubsub.listen():
    if msg["type"] == "message":
        print(f"[PEDIDO CONFIRMADO] {msg['data']}", flush=True)
