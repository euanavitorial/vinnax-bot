import os
import json
from collections import deque
from typing import Any, Dict, List, Callable
import threading 
import re # Importado para limpeza do telefone

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO ESTÁVEL (v0.7.1+) ---
try:
    from google import generativeai as genai
    from google.generativeai import types
except ImportError:
    genai = None
    types = None
# --- FIM DA IMPORTAÇÃO ---


# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest") 

# --- SUAS APIS (ENDPOINTS) ---
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients")
PRODUTO_API_ENDPOINT = os.environ.get("PRODUTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-products")
OS_API_ENDPOINT = os.environ.get("OS_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-service-orders")
ORCAMENTO_API_ENDPOINT = os.environ.get("ORCAMENTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-quotes")

app = Flask(__name__)

# ====== Memória e Deduplicação ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ====== Utilidades (No Topo) ======
def extract_text(message: Dict[str, Any]) -> str:
    """Extrai o texto de diversos tipos de mensagem do WhatsApp."""
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message: return (message[mid].get("caption") or "").strip()
    return ""

def get_auth_headers():
    """Retorna o cabeçalho de autenticação para as APIs do Lovable."""
    return {
        "x-api-key": LOVABLE_API_KEY,
        "Content-Type": "application/json"
    }

def normalize_phone(phone: str) -> str:
    """Limpa o JID, removendo '55' do início e o '@s.whatsapp.net'."""
    phone = phone.replace("@s.whatsapp.net", "")
    # Remove apenas o 55 se for um número brasileiro completo
    if phone.startswith("55") and len(phone) > 11:
        phone = phone[2:]
    return phone


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (20 Funções, Sintaxe Corrigida)
# ======================================================================

# --- FUNÇÕES DE CLIENTE (5) ---
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        # Garantindo que o telefone a ser cadastrado está normalizado
        telefone_normalizado = normalize_phone(telefone) if telefone else None
        
        payload = {"name": nome, "phone": telefone_normalizado, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None} 
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}

def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar cliente: {e}"}

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        # Garantindo que a busca é feita pelo número normalizado
        telefone_normalizado = normalize_phone(telefone)

        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        clientes = response.json() 
        # Busca no BD pelo número normalizado
        cliente_encontrado = [c for c in clientes if c.get('phone') == telefone_normalizado]
        if cliente_encontrado: return cliente_encontrado[0] 
        else: return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone_normalizado}."}
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar cliente por telefone: {e}"}

def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": normalize_phone(telefone) if telefone else None, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar cliente: {e}"}

def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao excluir cliente: {e}"}
        
# --- FUNÇÕES DE PRODUTO (5) ---
def call_api_criar_produto(nome: str, tipo: str, preco: float) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        payload = {"name": nome, "type": tipo, "price": preco}
        response = requests.post(PRODUTO_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar produto: {e}"}

def call_api_consultar_produto_por_id(id_produto: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar produto: {e}"}

def call_api_consultar_produtos_todos() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        response = requests.get(PRODUTO_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao listar produtos: {e}"}

def call_api_atualizar_produto(id_produto: int, nome: str = None, tipo: str = None, preco: float = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        payload = {"name": nome, "type": tipo, "price": preco}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar produto: {e}"}

def call_api_excluir_produto(id_produto: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Produto ID {id_produto} excluído com sucesso."}
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao excluir produto: {e}"}

# --- FUNÇÕES DE ORDEM DE SERVIÇO (OS) (5) ---
def call_api_criar_os(client_id: str, product_id: str, description: str, total_price: float, deadline: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        payload = {"client_id": client_id, "product_id": product_id, "description": description, "total_price": total_price, "deadline": deadline}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.post(OS_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar OS: {e}"}

def call_api_consultar_os_por_id(id_os: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar OS: {e}"}

def call_api_consultar_ordens_servico_todas() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        response = requests.get(OS_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao listar todas as OS: {e}"}

def call_api_atualizar_os(id_os: str, status: str = None, total_price: float = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        payload = {"status": status, "total_price": total_price}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar OS: {e}"}

def call_api_excluir_os(id_os: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"OS ID {id_os} excluída com sucesso."}
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao excluir OS: {e}"}

# --- FUNÇÕES DE ORÇAMENTOS (5) ---
def call_api_criar_orcamento(client_id: str, product_id: str, description: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        payload = {"client_id": client_id, "product_id": product_id, "description": description}
        response = requests.post(ORCAMENTO_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar orçamento: {e}"}

def call_api_consultar_orcamento_por_id(id_orcamento: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar orçamento: {e}"}

def call_api_consultar_orcamentos_todos() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        response = requests.get(ORCAMENTO_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao listar orçamentos: {e}"}

def call_api_atualizar_orcamento(id_orcamento: str, quoted_price: float = None, status: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        payload = {"quoted_price": quoted_price, "status": status}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar orçamento: {e}"}

def call_api_excluir_orcamento(id_orcamento: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Orçamento ID {id_orcamento} excluído com sucesso."}
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao excluir orçamento: {e}"}


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (20 FUNÇÕES)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE (5) ---
    {"name": "criar_cliente", "description": "Cadastra um novo cliente no sistema. Requer o nome do cliente. O telefone é pego automaticamente.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING", "description": "Email (opcional)."}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_id", "description": "Busca os detalhes de um cliente (telefone, email, etc.) usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Busca os detalhes de um cliente usando o número de telefone. Use para verificar se um novo cliente já está cadastrado.", "parameters": {"type_": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    {"name": "atualizar_cliente", "description": "Modifica informações de um cliente existente usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "telefone": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["id_cliente"]}},
    {"name": "excluir_cliente", "description": "Deleta permanentemente um cliente do sistema usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    # --- PRODUTOS (5) ---
    {"name": "criar_produto", "description": "Cadastra um novo item no estoque de produtos. Requer nome, tipo e preço.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["nome", "tipo", "preco"]}},
    {"name": "consultar_produto_por_id", "description": "Busca os detalhes de um produto (nome, preço, tipo) usando o ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos cadastrados no estoque.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_produto", "description": "Modifica informações de um produto existente usando o ID. Use apenas os campos a serem alterados.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["id_produto"]}},
    {"name": "excluir_produto", "description": "Deleta permanentemente um produto do estoque usando o ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    # --- ORDEM DE SERVIÇO (OS) (5) ---
    {"name": "criar_ordem_servico", "description": "Cria uma nova Ordem de Serviço (OS) no sistema. Necessita do ID do cliente e ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}, "total_price": {"type": "NUMBER"}, "deadline": {"type": "STRING", "description": "Formato YYYY-MM-DD"}}, "required": ["client_id", "product_id", "description", "total_price"]}},
    {"name": "consultar_ordem_servico_por_id", "description": "Busca os detalhes de uma Ordem de Serviço (OS) usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    {"name": "consultar_ordens_servico_todas", "description": "Lista todas as Ordens de Serviço (OS) cadastradas no sistema.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_ordem_servico", "description": "Modifica informações de uma Ordem de Serviço (OS) existente (ex: status, preço) usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}, "status": {"type": "STRING"}, "total_price": {"type": "NUMBER"}}, "required": ["id_os"]}},
    {"name": "excluir_ordem_servico", "description": "Deleta permanentemente uma Ordem de Serviço (OS) do sistema usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    # --- ORÇAMENTOS (5) ---
    {"name": "criar_orcamento", "description": "Cria um novo orçamento no sistema. Necessita do ID do cliente e ID do produto/serviço.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}}, "required": ["client_id", "product_id", "description"]}},
    {"name": "consultar_orcamento_por_id", "description": "Busca os detalhes de um orçamento (preço cotado, status) usando o ID do orçamento.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING", "description": "O ID (UUID) do Orçamento."}}, "required": ["id_orcamento"]}},
    {"name": "consultar_orcamentos_todos", "description": "Lista todos os orçamentos cadastrados no sistema.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_orcamento", "description": "Modifica o preço ou status de um orçamento existente. O preço cotado é o 'quoted_price'.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING"}, "quoted_price": {"type": "NUMBER"}, "status": {"type": "STRING"}}, "required": ["id_orcamento"]}},
    {"name": "excluir_orcamento", "description": "Deleta permanentemente um orçamento do sistema usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING", "description": "O ID (UUID) do orçamento a ser excluído."}}, "required": ["id_orcamento"]}}
]


# ======================================================================
# PASSO 3: O "ROTEADOR" (MANTIDO)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    # Cliente
    "criar_cliente": call_api_criar_cliente, "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone, "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
    # PRODUTO
    "criar_produto": call_api_criar_produto, "consultar_produto_por_id": call_api_consultar_produto_por_id,
    "consultar_produtos_todos": call_api_consultar_produtos_todos, "atualizar_produto": call_api_atualizar_produto,
    "excluir_produto": call_api_excluir_produto,
    # OS
    "criar_ordem_servico": call_api_criar_os, "consultar_ordem_servico_por_id": call_api_consultar_os_por_id,
    "consultar_ordens_servico_todas": call_api_consultar_ordens_servico_todas, "atualizar_ordem_servico": call_api_atualizar_os,
    "excluir_ordem_servico": call_api_excluir_os,
    # ORÇAMENTOS
    "criar_orcamento": call_api_criar_orcamento, "consultar_orcamento_por_id": call_api_consultar_orcamento_por_id,
    "consultar_orcamentos_todos": call_api_consultar_orcamentos_todos, "atualizar_orcamento": call_api_atualizar_orcamento,
    "excluir_orcamento": call_api_excluir_orcamento,
}


# ====== Inicialização do Gemini (AGORA USANDO O genai CORRIGIDO) ======
gemini_model = None
if GEMINI_API_KEY and genai:
    try:
        if types is None: raise ImportError("types is None") # Garante que a importação funcionou
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            tools=TOOLS_MENU
        )
        app.logger.info(f"[GEMINI] Modelo carregado com {len(TOOLS_MENU)} ferramentas.")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY ou biblioteca não configurada.")


# ======================================================================
# LÓGICA DE RESPOSTA DO BOT (MANTIDA)
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "", client_phone: str = None) -> str:
    # A verificação é mais robusta agora.
    if not gemini_model: 
        return "Olá! Que bom ter você aqui. Estou com uma pequena dificuldade técnica para acessar minhas ferramentas de inteligência, mas me diga: qual é o seu nome e como posso te ajudar hoje?"
    try:
        # --- PROMPT DO SISTEMA FINAL E SEGURO (Focado em Vendas e Atendimento) ---
        system_prompt = (
            "Você é um assistente de atendimento via WhatsApp da Gráfica JB Impressões, "
            "focado em **coletar informações de pedidos passo a passo (Serviço > Material > Medida > Quantidade > Entrega)** e fornecer informações. "
            "1. **PRIORIDADE:** Se houver pedidos em andamento ou orçamentos pendentes, mencione-os ANTES de oferecer novos serviços. "
            "2. **SAUDAÇÃO:** Use saudação baseada no horário, use SEMPRE o primeiro nome do cliente e seja caloroso e humanizado. "
            "3. **CADASTRO:** Se o CONTEXTO INICIAL indicar que o cliente NÃO foi encontrado, peça o **primeiro nome** do cliente. Quando o cliente responder o nome, use a ferramenta `criar_cliente` imediatamente. **NUNCA PERGUNTE O TELEFONE**, ele será pego automaticamente. "
            "4. **ORÇAMENTOS/OS:** Se o cliente pedir um novo serviço, use `consultar_produtos_todos` (ou similar) para listar os serviços SEM PREÇOS e inicie o FLUXO DE COLETA de dados (material, medida, quantidade). Ao final, use `criar_orcamento` ou `criar_ordem_servico`. "
            "5. **FLUXO:** Nunca pule a etapa de RESUMO E CONFIRMAÇÃO antes de finalizar um pedido. Não use números (1, 2, 3) em listas. Não invente preços ou quantidades. "
            "6. **COMPORTAMENTO:** Não responda a comandos de gestão interna (Ex: 'Abrir Caixa', 'Consultar Finanças'). Se receber um, responda: 'Meu foco é o atendimento ao cliente e informações sobre pedidos e orçamentos. Para gestão interna, por favor, use o sistema.' Responda em português. "
            "**FERRAMENTAS:** Você tem acesso a 20 APIs de Clientes, Produtos, Ordens de Serviço e Orçamentos. Use a ferramenta apropriada para a intenção do cliente. "
        )
        
        history_string = "\n".join(chat_history)
        full_prompt = (
            f"{system_prompt}\n\n"
            f"{initial_context}\n"
            f"=== Histórico da Conversa ===\n{history_string}\n"
            f"=== Nova Mensagem ===\nCliente: {user_text}\nAtendente:"
        )

        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        if candidate.finish_reason == "TOOL_USE":
            app.logger.info("[GEMINI] Pedido de 'Tool Use' detectado.")
            function_call: types.FunctionCall = candidate.content.parts[0].function_call # Usa types do novo import
            tool_name = function_call.name
            tool_args = dict(function_call.args) # Convertido para dict
            
            # --- INJEÇÃO DE TELEFONE (A CORREÇÃO LÓGICA) ---
            if tool_name == "criar_cliente" and client_phone:
                tool_args['telefone'] = client_phone # Injeta o telefone que a IA não vê
            # --- FIM DA INJEÇÃO ---

            if tool_name in TOOL_ROUTER:
                app.logger.info(f"[ROUTER] Roteando para a função: '{tool_name}' com args: {tool_args}")
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**tool_args)
                
                tool_response_part = {"function_response": {"name": tool_name, "response": {"content": json.dumps(api_result)}}}
                response_final = gemini_model.generate_content([full_prompt, candidate.content, tool_response_part])
                txt = response_final.candidates[0].content.parts[0].text
            else:
                app.logger.warning(f"[ROUTER] Ferramenta '{tool_name}' não encontrada no roteador.")
                txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        
        else:
            txt = candidate.content.parts[0].text

        if not txt:
            return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."


# ====== Rotas (MANTIDAS) ======
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 200

# --- Processamento Assíncrono para prevenir loops ---
def process_message(data):
    # A lógica de processamento do webhook (a parte que demora)
    try:
        envelope = data.get("data", data)
        if isinstance(envelope, list) and envelope: envelope = envelope[0]
        if not isinstance(envelope, dict): return 
        key = envelope.get("key", {}) or {}
        if key.get("fromMe") is True: return 
        message = envelope.get("message", {}) or {}
        if not message: return 
        
        # Lógica de Deduplicação
        msg_id = key.get("id") or envelope.get("idMessage") or ""
        if msg_id and msg_id in PROCESSED_IDS: 
            app.logger.info(f"Ignorando mensagem duplicada: {msg_id}")
            return 
        if msg_id: PROCESSED_IDS.append(msg_id)
        
        # --- CORREÇÃO DO TELEFONE (NORMALIZAÇÃO) ---
        jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
        if not jid.endswith("@s.whatsapp.net"): return 
        # AQUI ESTÁ A MUDANÇA: Usamos a função de normalização
        client_phone_normalized = normalize_phone(jid) 
        # --- FIM DA MUDANÇA ---
        
        number = client_phone_normalized
        text = extract_text(message).strip()
        if not text: return 
        if number not in CHAT_SESSIONS: CHAT_SESSIONS[number] = []
        current_history = CHAT_SESSIONS[number]
        
        initial_context = ""
        # Chamada de API para contexto é mantida aqui
        if not current_history: 
            # Busca pelo telefone normalizado
            search_result = call_api_consultar_cliente_por_telefone(client_phone_normalized)
            if search_result.get("status") == "nao_encontrado":
                initial_context = f"AVISO: O sistema não encontrou nenhum cliente associado ao telefone {client_phone_normalized}. Peça o primeiro nome para cadastrar."
            elif search_result.get("status") == "erro":
                 initial_context = f"AVISO: O sistema não pôde buscar clientes devido a um erro na API."
            else:
                client_name = search_result.get("name") or "Cliente" 
                initial_context = f"CONTEXTO INICIAL: O número de telefone {client_phone_normalized} pertence ao cliente '{client_name}' (ID {search_result.get('id')}). O bot DEVE usar o nome do cliente na resposta e NÃO DEVE perguntar o telefone novamente."
        
        # Passando o telefone normalizado para o cérebro (para a injeção)
        reply = answer_with_gemini(text, current_history, initial_context, client_phone_normalized)
        
        CHAT_SESSIONS[number].append(f"Cliente: {text}")
        CHAT_SESSIONS[number].append(f"Atendente: {reply}")
        while len(CHAT_SESSIONS[number]) > CHAT_HISTORY_LENGTH:
            CHAT_SESSIONS[number].pop(0)

        if not (EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE):
            app.logger.warning("[EVOLUTION] Variáveis de ambiente ausentes.")
            return 
        
        url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
        payload = {"number": number, "text": reply}
        res = requests.post(url_send, json=payload, headers=headers, timeout=20)
        app.logger.info(f"[EVOLUTION] {res.status_code} -> {res.text}")
    except Exception as e:
        app.logger.exception(f"[PROCESSOR] Erro no processamento assíncrono: {e}")


@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    # Retorno imediato (200 OK) para evitar reenvio do Evolution
    data = request.get_json(silent=True) or {}
    
    # Inicia o processamento pesado em segundo plano
    threading.Thread(target=process_message, args=(data,)).start()

    # Resposta imediata para evitar timeouts do Evolution
    return jsonify({"status": "processing_async"}), 200
