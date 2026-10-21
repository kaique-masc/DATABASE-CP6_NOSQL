"""Popula o MongoDB com 12 eventos, 24 usuários e 60 pedidos.

Uso (com o compose no ar):  docker compose exec api python seed.py
"""
import random
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.database import criar_indices, eventos, pedidos, r, usuarios

random.seed(42)

CIDADES = ["São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre"]

EVENTOS_BASE = [
    ("Rock in Rio Night", "show", ["Banda Aurora", "DJ Neon"], "Parque Olímpico"),
    ("Festival Sertanejo Brasil", "show", ["Duo Estrada", "Maria Viola"], "Arena Central"),
    ("Noite do Jazz", "show", ["Quarteto Blue", "Ana Sax"], "Teatro Municipal"),
    ("Stand-up Comedy Tour", "teatro", ["Carlos Riso"], "Teatro Vivo"),
    ("O Fantasma da Ópera", "teatro", ["Cia. Lírica Nacional"], "Teatro Alfa"),
    ("Final do Campeonato Estadual", "esporte", [], "Estádio Municipal"),
    ("Maratona Cidade Viva", "esporte", [], "Av. Principal"),
    ("Tech Summit 2026", "conferencia", ["Keynote IA", "Painel Dados"], "Centro de Convenções"),
    ("Festival de Cinema Indie", "festival", ["Mostra Curtas"], "Cine Arte"),
    ("Lollapalooza Warm-up", "festival", ["The Waves", "Lia Moon", "DJ Pulse"], "Autódromo"),
    ("Orquestra Sinfônica em Concerto", "show", ["Orquestra Sinfônica"], "Sala São Paulo"),
    ("Feira Gastronômica", "festival", ["Chefs Convidados"], "Pavilhão de Exposições"),
]

NOMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gabriela", "Hugo", "Isabela", "João",
    "Karina", "Lucas", "Marina", "Nicolas", "Olívia", "Paulo", "Queila", "Rafael", "Sofia",
    "Thiago", "Ursula", "Vitor", "Wesley", "Yasmin",
]


def main() -> None:
    for c in (eventos, usuarios, pedidos):
        c.drop()
    r.flushdb()
    criar_indices()

    agora = datetime.now(timezone.utc)

    # ---- eventos (setores embutidos)
    docs_eventos = []
    for i, (nome, cat, atracoes, endereco) in enumerate(EVENTOS_BASE):
        docs_eventos.append(
            {
                "_id": ObjectId(),
                "nome": nome,
                "categoria": cat,
                "data": agora + timedelta(days=15 + i * 7),
                "local": {"cidade": CIDADES[i % len(CIDADES)], "endereco": endereco},
                "atracoes": atracoes,
                "setores": [
                    {"nome": "Pista", "preco": 120.0 + i * 10, "capacidade": 500, "vendidos": 0},
                    {"nome": "Camarote", "preco": 300.0 + i * 15, "capacidade": 100, "vendidos": 0},
                    {"nome": "VIP", "preco": 550.0 + i * 20, "capacidade": 40, "vendidos": 0},
                ],
            }
        )

    # ---- usuários
    docs_usuarios = []
    for i, nome in enumerate(NOMES):
        docs_usuarios.append(
            {
                "_id": ObjectId(),
                "nome": f"{nome} Silva",
                "email": f"{nome.lower()}.silva{i}@exemplo.com",
                "cidade": random.choice(CIDADES),
                "dataCadastro": agora - timedelta(days=random.randint(1, 400)),
            }
        )

    # ---- pedidos (vendidos dos setores acompanha os pedidos confirmados)
    docs_pedidos = []
    for _ in range(60):
        ev = random.choice(docs_eventos)
        us = random.choice(docs_usuarios)
        setor = random.choice(ev["setores"])
        qtd = random.randint(1, 4)
        status = random.choices(["confirmado", "cancelado"], weights=[85, 15])[0]
        if status == "confirmado":
            setor["vendidos"] += qtd
        docs_pedidos.append(
            {
                "usuarioId": us["_id"],
                "eventoId": ev["_id"],
                "setor": setor["nome"],
                "quantidade": qtd,
                "valorTotal": round(qtd * setor["preco"], 2),
                "status": status,
                "criadoEm": agora - timedelta(hours=random.randint(1, 720)),
            }
        )

    eventos.insert_many(docs_eventos)
    usuarios.insert_many(docs_usuarios)
    pedidos.insert_many(docs_pedidos)
    print(f"Seed concluído: {eventos.count_documents({})} eventos, "
          f"{usuarios.count_documents({})} usuários, {pedidos.count_documents({})} pedidos.")


if __name__ == "__main__":
    main()
