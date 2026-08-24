import json
from fastapi import APIRouter, HTTPException, Path, Body
from pydantic import BaseModel
from typing import List
import csv
import threading
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv
from app.services.email_service import enviar_email_alerta


load_dotenv(override=True)
router = APIRouter()

CSV_FILE = "app/data/products.csv"
MOV_CSV_FILE = "app/data/movimentacoes.csv"

# RLock e nao Lock: os endpoints seguram o lock durante todo o ciclo
# ler-alterar-gravar, e write_products_to_csv/write_movimentacoes readquirem
# o mesmo lock por dentro. Com Lock simples isso travaria.
lock = threading.RLock()
mov_lock = threading.RLock()

tz = pytz.timezone("America/Sao_Paulo")


# =========================
# MODELS
# =========================

class ProductBase(BaseModel):
    id: int = None
    nome: str
    estoque_atual: int = 0
    estoque_4andar: int = 0
    estoque_5andar: int = 0
    limite_alerta_geral: int = 0
    email_alerta_geral: bool = False
    desfalque: int = 0


class ProductResponse(ProductBase):
    id: int



class Movimentacao(BaseModel):
    id_produto: int
    tipo: str
    quantidade: int
    andar: str
    timestamp: str


class ConsumoData(BaseModel):
    id: int
    quantidade: int
    andar: str


# =========================
# CSV HELPERS
# =========================

def read_products_from_csv() -> List[ProductResponse]:
    products = []
    try:
        with open(CSV_FILE, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile, delimiter=";")
            for row in reader:
                products.append(ProductResponse(
                    id=int(row["id"]),
                    nome=row["nome"],
                    estoque_atual=int(row["estoque_atual"]),
                    estoque_4andar=int(row["estoque_4andar"]),
                    estoque_5andar=int(row["estoque_5andar"]),
                    limite_alerta_geral=int(row["limite_alerta_geral"]),
                    email_alerta_geral=row["email_alerta_geral"] == "True",
                    desfalque=int(row["desfalque"]) if "desfalque" in row else 0
                ))
    except FileNotFoundError:
        pass
    return products


def write_products_to_csv(products: List[ProductResponse]):
    with lock:
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "id", "nome",
                "estoque_atual", "estoque_4andar", "estoque_5andar","limite_alerta_geral",
                "email_alerta_geral", "desfalque"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=";")
            writer.writeheader()
            for p in products:
                writer.writerow(p.dict())


def read_movimentacoes() -> List[Movimentacao]:
    movs = []
    try:
        with open(MOV_CSV_FILE, newline="", encoding="latin-1") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                movs.append(Movimentacao(
                    id_produto=int(row["id_produto"]),
                    tipo=row["tipo"],
                    quantidade=int(row["quantidade"]),
                    andar=row["andar"],
                    timestamp=row["timestamp"]
                ))
    except FileNotFoundError:
        pass
    return movs


def write_movimentacoes(movs: List[Movimentacao]):
    with mov_lock:
        with open(MOV_CSV_FILE, "w", newline="", encoding="latin-1") as csvfile:
            fieldnames = ["id_produto", "tipo", "quantidade", "andar", "timestamp"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for m in movs:
                writer.writerow(m.dict())


# =========================
# ALERTA DE EMAIL
# =========================

def verificar_e_enviar_alerta(produto: ProductResponse):
    destinatarios = os.getenv("EMAIL_DESTINO",[])
    lista_emails = json.loads(destinatarios)
    
    # Apenas verifica se o estoque está no limite e envia alerta
    if produto.estoque_atual <= produto.limite_alerta_geral:
        enviar_email_alerta(lista_emails, produto.nome, "Estoque Geral",produto.estoque_atual)


# =========================
# ENDPOINTS
# =========================

@router.post("/products", response_model=ProductResponse)
def create_product(product: ProductBase):
    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        new_id = max([p.id for p in products], default=0) + 1

        data = product.dict()
        if data.get("desfalque", 0) > 0:
            if data["estoque_atual"] < data["desfalque"]:
                raise HTTPException(400, "Desfalque não pode ser maior que o estoque inicial")
            data["estoque_atual"] -= data["desfalque"]
            movs.append(Movimentacao(
                id_produto=new_id,
                tipo="desfalque",
                quantidade=data["desfalque"],
                andar="geral",
                timestamp=datetime.now(tz).isoformat()
            ))
            write_movimentacoes(movs)

        new_product = ProductResponse(id=new_id, **data)
        products.append(new_product)
        write_products_to_csv(products)

    return new_product


@router.get("/products", response_model=List[ProductResponse])
def list_products():
    return read_products_from_csv()


@router.put("/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int = Path(...), product: ProductBase = Body(...)):
    """Atualiza o produto. Subir um andar ou o desfalque consome do estoque geral.

    O saldo enviado em `estoque_atual` é o ponto de partida; sobre ele aplicamos o
    que os andares e o desfalque variaram. Baixar um andar devolve ao geral, de modo
    que o total (geral + andares + desfalque) só muda se `estoque_atual` for editado
    de propósito.
    """
    for campo in ("estoque_atual", "estoque_4andar", "estoque_5andar", "desfalque"):
        if getattr(product, campo) < 0:
            raise HTTPException(400, f"{campo} não pode ser negativo")

    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        produto = next((p for p in products if p.id == product_id), None)
        if not produto:
            raise HTTPException(404, "Produto não encontrado")

        # (rótulo do balde, quanto variou)
        ajustes = [
            ("4", product.estoque_4andar - produto.estoque_4andar),
            ("5", product.estoque_5andar - produto.estoque_5andar),
            ("desfalque", product.desfalque - produto.desfalque),
        ]
        consumido = sum(delta for _, delta in ajustes)

        novo_geral = product.estoque_atual - consumido
        if novo_geral < 0:
            raise HTTPException(
                400,
                f"Estoque geral insuficiente: o ajuste consome {consumido} unidade(s) "
                f"e o saldo ficaria em {novo_geral}.",
            )

        ajustes.append(("geral", product.estoque_atual - produto.estoque_atual))

        produto.nome = product.nome
        produto.estoque_atual = novo_geral
        produto.estoque_4andar = product.estoque_4andar
        produto.estoque_5andar = product.estoque_5andar
        produto.limite_alerta_geral = product.limite_alerta_geral
        produto.email_alerta_geral = product.email_alerta_geral
        produto.desfalque = product.desfalque
        write_products_to_csv(products)

        # Rastro do ajuste: sem isso o saldo mudaria sem explicação no histórico.
        agora = datetime.now(tz).isoformat()
        for balde, delta in ajustes:
            if delta:
                movs.append(Movimentacao(
                    id_produto=product_id,
                    tipo="ajuste manual (entrada)" if delta > 0 else "ajuste manual (saida)",
                    quantidade=abs(delta),
                    andar=balde,
                    timestamp=agora
                ))
        write_movimentacoes(movs)

    verificar_e_enviar_alerta(produto)

    return produto


@router.post("/products/entrada")
def adicionar_estoque(id: int = Body(...), quantidade: int = Body(...)):
    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        produto = next((p for p in products if p.id == id), None)
        if not produto:
            raise HTTPException(404, "Produto não encontrado")

        produto.estoque_atual += quantidade
        write_products_to_csv(products)

        movs.append(Movimentacao(
            id_produto=id,
            tipo="adição de estoque",
            quantidade=quantidade,
            andar="geral",
            timestamp=datetime.now(tz).isoformat()
        ))
        write_movimentacoes(movs)

    # Fora do lock: o alerta e' uma chamada SMTP e nao pode segurar as escritas
    # dos outros usuarios enquanto o servidor de e-mail responde.
    verificar_e_enviar_alerta(produto)

    return {"message": "Entrada registrada", "produto": produto}


@router.post("/products/retirada")
def retirada(id: int = Body(...), quantidade: int = Body(...), andar: str = Body(...)):
    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        produto = next((p for p in products if p.id == id), None)
        if not produto:
            raise HTTPException(404, "Produto não encontrado")

        if produto.estoque_atual < quantidade:
            raise HTTPException(400, "Estoque geral insuficiente")

        if andar == "4":
            produto.estoque_4andar += quantidade
        elif andar == "5":
            produto.estoque_5andar += quantidade
        else:
            raise HTTPException(400, "Andar inválido")

        produto.estoque_atual -= quantidade
        write_products_to_csv(products)

        movs.append(Movimentacao(
            id_produto=id,
            tipo="Andar Destinado",
            quantidade=quantidade,
            andar=andar,
            timestamp=datetime.now(tz).isoformat()
        ))
        write_movimentacoes(movs)

    verificar_e_enviar_alerta(produto)

    return {"message": "Retirada realizada", "produto": produto}


@router.post("/products/consumo")
def consumo(data: ConsumoData):
    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        produto = next((p for p in products if p.id == data.id), None)
        if not produto:
            raise HTTPException(404, "Produto não encontrado")

        if data.andar == "4":
            if produto.estoque_4andar < data.quantidade:
                raise HTTPException(400, "Estoque insuficiente 4º andar")
            produto.estoque_4andar -= data.quantidade
        elif data.andar == "5":
            if produto.estoque_5andar < data.quantidade:
                raise HTTPException(400, "Estoque insuficiente 5º andar")
            produto.estoque_5andar -= data.quantidade
        else:
            raise HTTPException(400, "Andar inválido")

        # O estoque geral nao muda aqui: a unidade ja saiu dele na retirada para o
        # andar. Abater de novo contaria a mesma unidade duas vezes.
        write_products_to_csv(products)

        movs.append(Movimentacao(
            id_produto=data.id,
            tipo="consumo",
            quantidade=data.quantidade,
            andar=data.andar,
            timestamp=datetime.now(tz).isoformat()
        ))
        write_movimentacoes(movs)

    verificar_e_enviar_alerta(produto)

    return {"message": "Consumo registrado", "produto": produto}


@router.post("/products/desfalque")
def registra_desfalque(id: int = Body(...), quantidade: int = Body(...)):
    with lock:
        products = read_products_from_csv()
        movs = read_movimentacoes()

        produto = next((p for p in products if p.id == id), None)
        if not produto:
            raise HTTPException(404, "Produto não encontrado")

        if produto.estoque_atual < quantidade:
            raise HTTPException(400, "Estoque geral insuficiente para registrar desfalque")

        produto.desfalque += quantidade
        produto.estoque_atual -= quantidade
        write_products_to_csv(products)

        movs.append(Movimentacao(
            id_produto=id,
            tipo="desfalque",
            quantidade=quantidade,
            andar="geral",
            timestamp=datetime.now(tz).isoformat()
        ))
        write_movimentacoes(movs)

    verificar_e_enviar_alerta(produto)

    return {"message": "Desfalque registrado", "produto": produto}


@router.get("/movimentacoes", response_model=List[Movimentacao])
def listar_movimentacoes():
    return read_movimentacoes()


@router.get("/products/{product_id}/movimentacoes", response_model=List[Movimentacao])
def listar_movimentacoes_produto(product_id: int = Path(...)):
    return [m for m in read_movimentacoes() if m.id_produto == product_id]
