import os
import json
from collections import deque
from typing import Any, Dict, List, Callable
import threading 
import re 
import sys 
from requests.exceptions import HTTPError 

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO ESTÁVEL (v0.7.1+) ---
try:
    from google import generativeai as genai
    from google.generativeai import types
except ImportError:
    print("[ERRO FATAL] Biblioteca google.generativeai não encontrada.", file=sys.stderr)
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
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message: return (message[mid].get("caption") or "").strip()
    return ""

def get_auth_headers():
    return {
        "x-api-key": LOVABLE_API_KEY,
        "Content-Type": "application/json"
    }

def normalize_phone(phone: str) -> str:
    phone = re.sub(r'@s\.whatsapp\.net$', '', phone) 
    if phone.startswith("55"):
        phone = phone[2:]
    if len(phone) >= 11 and phone[2] == "0":
        phone = phone[:2] + phone[3:]
    return phone


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API 
# ======================================================================

def log_api_error(e: Exception, function_name: str) -> Dict[str, Any]:
    if isinstance(e, HTTPError):
        error_message = f"Erro {e.response.status_code}: {e.response.text}"
    else:
        error_message = str(e)
    print(f"[API_CALL] Erro em {function_name}: {error_message}", file=sys.stderr)
    return {"status": "erro", "mensagem": f"Erro interno ao chamar {function_name}: {error_message}"}

# --- FUNÇÕES DE CLIENTE ---
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        telefone_normalizado = normalize_phone(telefone) if telefone else None
        payload = {"name": nome, "phone": telefone_normalizado, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None} 
        print(f"[API_CALL] Enviando para criar_cliente: {json.dumps(payload)}", file=sys.stderr)
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_cliente")

def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_cliente_por_id")

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): 
        print("[API_CALL] Chaves da API de Cliente não configuradas.", file=sys.stderr)
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        telefone_normalizado = normalize_phone(telefone)
        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        clientes = response.json() 
        if not isinstance(clientes, list):
             return {"status": "erro", "mensagem": "Resposta inesperada da API de Clientes."}
        cliente_encontrado = [c for c in clientes if c.get('phone') == telefone_normalizado]
        if cliente_encontrado: return cliente_encontrado[0] 
        else: return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone_normalizado}."}
    except Exception as e: return log_api_error(e, "consultar_cliente_por_telefone")

def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": normalize_phone(telefone) if telefone else None, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "atualizar_cliente")

def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e: return log_api_error(e, "excluir_cliente")
        
# --- FUNÇÕES DE PRODUTO ---
def call_api_criar_produto(nome: str, tipo: str, preco: float) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        payload = {"name": nome, "type": tipo, "price": preco}
        response = requests.post(PRODUTO_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_produto")

def call_api_consultar_produto_por_id(id_produto: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_produto_por_id")

def call_api_consultar_produtos_todos() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        response = requests.get(PRODUTO_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "listar_produtos")

def call_api_atualizar_produto(id_produto: int, nome: str = None, tipo: str = None, preco: float = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        payload = {"name": nome, "type": tipo, "price": preco}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "atualizar_produto")

def call_api_excluir_produto(id_produto: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and PRODUTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Produto não configurada."}
    try:
        url = f"{PRODUTO_API_ENDPOINT}/{id_produto}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Produto ID {id_produto} excluído com sucesso."}
        return response.json()
    except Exception as e: return log_api_error(e, "excluir_produto")

# --- FUNÇÕES DE ORDEM DE SERVIÇO (OS) ---
def call_api_criar_os(client_id: str, product_id: str, description: str, total_price: float, deadline: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        payload = {"client_id": client_id, "product_id": product_id, "description": description, "total_price": total_price, "deadline": deadline}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.post(OS_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_os")

def call_api_consultar_os_por_id(id_os: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_os_por_id")

def call_api_consultar_ordens_servico_todas() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        response = requests.get(OS_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "listar_os")

def call_api_atualizar_os(id_os: str, status: str = None, total_price: float = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        payload = {"status": status, "total_price": total_price}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "atualizar_os")

def call_api_excluir_os(id_os: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and OS_API_ENDPOINT): return {"status": "erro", "mensagem": "API de OS não configurada."}
    try:
        url = f"{OS_API_ENDPOINT}/{id_os}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"OS ID {id_os} excluída com sucesso."}
        return response.json()
    except Exception as e: return log_api_error(e, "excluir_os")

# --- FUNÇÕES DE ORÇAMENTOS ---
def call_api_criar_orcamento(client_id: str, product_id: str, description: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        payload = {"client_id": client_id, "product_id": product_id, "description": description}
        response = requests.post(ORCAMENTO_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_orcamento")

def call_api_consultar_orcamento_por_id(id_orcamento: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_orcamento_por_id")

def call_api_consultar_orcamentos_todos() -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        response = requests.get(ORCAMENTO_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "listar_orcamentos")

def call_api_atualizar_orcamento(id_orcamento: str, quoted_price: float = None, status: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        payload = {"quoted_price": quoted_price, "status": status}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "atualizar_orcamento")

def call_api_excluir_orcamento(id_orcamento: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and ORCAMENTO_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Orçamento não configurada."}
    try:
        url = f"{ORCAMENTO_API_ENDPOINT}/{id_orcamento}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Orçamento ID {id_orcamento} excluído com sucesso."}
        return response.json()
    except Exception as e: return log_api_error(e, "excluir_orcamento")


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (20 FUNÇÕES)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE ---
    {"name": "criar_cliente", "description": "Cadastra um novo cliente no sistema. Requer o nome do cliente. O telefone é pego automaticamente.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING", "description": "Email (opcional)."}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_id", "description": "Busca os detalhes de um cliente usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Busca os detalhes de um cliente usando o número de telefone.", "parameters": {"type_": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    {"name": "atualizar_cliente", "description": "Modifica informações de um cliente existente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "telefone": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["id_cliente"]}},
    {"name": "excluir_cliente", "description": "Deleta permanentemente um cliente do sistema.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    # --- PRODUTOS ---
    {"name": "criar_produto", "description": "Cadastra um novo item no estoque.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["nome", "tipo", "preco"]}},
    {"name": "consultar_produto_por_id", "description": "Busca os detalhes de um produto usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos cadastrados no estoque.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_produto", "description": "Modifica informações de um produto existente.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["id_produto"]}},
    {"name": "excluir_produto", "description": "Deleta permanentemente um produto do estoque.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    # --- ORDEM DE SERVIÇO ---
    {"name": "criar_ordem_servico", "description": "Cria uma nova Ordem de Serviço.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}, "total_price": {"type": "NUMBER"}, "deadline": {"type": "STRING", "description": "Formato YYYY-MM-DD"}}, "required": ["client_id", "product_id", "description", "total_price"]}},
    {"name": "consultar_ordem_servico_por_id", "description": "Busca os detalhes de uma Ordem de Serviço.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    {"name": "consultar_ordens_servico_todas", "description": "Lista todas as Ordens de Serviço cadastradas.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_ordem_servico", "description": "Modifica informações de uma Ordem de Serviço existente.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}, "status": {"type": "STRING"}, "total_price": {"type": "NUMBER"}}, "required": ["id_os"]}},
    {"name": "excluir_ordem_servico", "description": "Deleta permanentemente uma Ordem de Serviço.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    # --- ORÇAMENTOS ---
    {"name": "criar_orcamento", "description": "Cria um novo orçamento.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}}, "required": ["client_id", "product_id", "description"]}},
    {"name": "consultar_orcamento_por_id", "description": "Busca os detalhes de um orçamento.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING"}}, "required": ["id_orcamento"]}},
    {"name": "consultar_orcamentos_todos", "description": "Lista todos os orçamentos cadastrados.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_orcamento", "description": "Modifica o preço ou status de um orçamento.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING"}, "quoted_price": {"type": "NUMBER"}, "status": {"type": "STRING"}}, "required": ["id_orcamento"]}},
    {"name": "excluir_orcamento", "description": "Deleta permanentemente um orçamento.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING"}}, "required": ["id_orcamento"]}}
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


# ====== Inicialização do Gemini (MANTIDO) ======
gemini_model = None
if GEMINI_API_KEY and genai:
    try:
        if types is None: raise ImportError("types is None")
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME, tools=TOOLS_MENU)
        print(f"[GEMINI] Modelo carregado com {len(TOOLS_MENU)} ferramentas.", file=sys.stderr)
    except Exception as e:
        print(f"[GEMINI] Erro ao inicializar SDK: {e}", file=sys.stderr)
else:
    print("[GEMINI] GEMINI_API_KEY ou biblioteca não configurada.", file=sys.stderr)


# ======================================================================
# LÓGICA DE RESPOSTA DO BOT (COM CORREÇÃO DE MEMÓRIA/CONTEXTO)
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "", client_phone: str = None) -> str:
    if not gemini_model: 
        return "Olá! Que bom ter você aqui. Estou com uma pequena dificuldade técnica para acessar minhas ferramentas de inteligência, mas me diga: qual é o seu nome e como posso te ajudar hoje?"
    try:
        system_prompt = (
            "Você é um assistente de atendimento via WhatsApp da Gráfica JB Impressões, "
            "focado em **coletar informações de pedidos passo a passo (Serviço > Material > Medida > Quantidade > Entrega)** e fornecer informações. "
            "1. **PRIORIDADE:** Se houver pedidos em andamento ou orçamentos pendentes, mencione-os ANTES de oferecer novos serviços. "
            "2. **SAUDAÇÃO:** Use saudação baseada no horário, use SEMPRE o primeiro nome do cliente e seja caloroso e humanizado. "
            "3. **CADASTRO (REGRA OBRIGATÓRIA):** Se o `CONTEXTO INICIAL` for o AVISO de 'cliente não encontrado', sua **ÚNICA TAREFA** é pedir o primeiro nome. Se a mensagem seguinte do cliente for *qualquer coisa que pareça um nome* (como 'Ana', 'João', 'Ana Vitória'), você **DEVE OBRIGATORIAMENTE** assumir que é o nome e chamar a ferramenta `criar_cliente` com esse nome. **NÃO RESPONDA 'Poderia repetir, por favor?'**. Use a ferramenta. "
            "4. **ORÇAMENTOS/OS:** Se o cliente pedir um novo serviço, use `consultar_produtos_todos` (ou similar) para listar os serviços SEM PREÇOS e inicie o FLUXO DE COLETA de dados. Ao final, use `criar_orcamento` ou `criar_ordem_servico`. "
            "5. **FLUXO:** Nunca pule a etapa de RESUMO E CONFIRMAÇÃO antes de finalizar um pedido. "
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
            print("[GEMINI] Pedido de 'Tool Use' detectado.", file=sys.stderr)
            function_call: types.FunctionCall = candidate.content.parts[0].function_call
            tool_name = function_call.name
            tool_args = dict(function_call.args)
            
            # --- INJEÇÃO DE TELEFONE (A CORREÇÃO LÓGICA) ---
            if tool_name == "criar_cliente" and client_phone:
                tool_args['telefone'] = client_phone
            # --- FIM DA INJEÇÃO ---

            if tool_name in TOOL_ROUTER:
                print(f"[ROUTER] Roteando para a função: '{tool_name}' com args: {tool_args}", file=sys.stderr)
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**tool_args)
                
                tool_response_part = {"function_response": {"name": tool_name, "response": {"content": json.dumps(api_result)}}}
                response_final = gemini_model.generate_content([full_prompt, candidate.content, tool_response_part])
                txt = response_final.candidates[0].content.parts[0].text
            else:
                print(f"[ROUTER] Ferramenta '{tool_name}' não encontrada no roteador.", file=sys.stderr)
                txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        else:
            txt = candidate.content.parts[0].text

        if not txt: return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        print(f"[GEMINI] Erro geral ao gerar resposta: {e}", file=sys.stderr)
        return "Desculpe, tive um problema para processar sua solicitação."


# ====== Rotas (MANTIDAS) ======
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 200

# --- Processamento Assíncrono (CORRIGIDO: VERIFICAÇÃO SEMPRE ATIVA) ---
def process_message(data):
    try:
        print("\n--- [PROCESSOR] Thread iniciada. ---", file=sys.stderr)
        envelope = data.get("data", data)
        if isinstance(envelope, list) and envelope: envelope = envelope[0]
        if not isinstance(envelope, dict): return 
        key = envelope.get("key", {}) or {}
        if key.get("fromMe") is True: return 
        message = envelope.get("message", {}) or {}
        if not message: return 
        
        msg_id = key.get("id") or envelope.get("idMessage") or ""
        if msg_id and msg_id in PROCESSED_IDS: 
            print(f"Ignorando mensagem duplicada: {msg_id}", file=sys.stderr)
            return 
        if msg_id: PROCESSED_IDS.append(msg_id)
        
        jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
        if not jid.endswith("@s.whatsapp.net"): return 
        
        client_phone_normalized = normalize_phone(jid) 
        number_to_reply = jid 
        
        text = extract_text(message).strip()
        if not text: 
            print("[PROCESSOR] Texto vazio, encerrando thread.", file=sys.stderr)
            return 
        
        if number_to_reply not in CHAT_SESSIONS: CHAT_SESSIONS[number_to_reply] = []
        current_history = CHAT_SESSIONS[number_to_reply]
        
        # --- MUDANÇA CRUCIAL: VERIFICA SEMPRE O STATUS DO CLIENTE ---
        # Removemos o "if not current_history:" para que o bot sempre saiba se o cliente existe.
        initial_context = ""
        print(f"[PROCESSOR] Buscando cliente: {client_phone_normalized}", file=sys.stderr)
        search_result = call_api_consultar_cliente_por_telefone(client_phone_normalized)
        
        if search_result.get("status") == "nao_encontrado":
            initial_context = f"AVISO: O sistema não encontrou nenhum cliente associado ao telefone {client_phone_normalized}. Peça o primeiro nome para cadastrar."
        elif search_result.get("status") == "erro":
                initial_context = f"AVISO: O sistema não pôde buscar clientes devido a um erro na API."
        else:
            client_name = search_result.get("name") or "Cliente" 
            initial_context = f"CONTEXTO INICIAL: O número de telefone {client_phone_normalized} pertence ao cliente '{client_name}' (ID {search_result.get('id')}). O bot DEVE usar o nome do cliente na resposta e NÃO DEVE perguntar o telefone novamente."
        # --- FIM DA MUDANÇA ---

        print("[PROCESSOR] Chamando answer_with_gemini...", file=sys.stderr)
        reply = answer_with_gemini(text, current_history, initial_context, client_phone_normalized)
        print(f"[PROCESSOR] Resposta do Gemini recebida: {reply[:50]}...", file=sys.stderr)
        
        CHAT_SESSIONS[number_to_reply].append(f"Cliente: {text}")
        CHAT_SESSIONS[number_to_reply].append(f"Atendente: {reply}")
        while len(CHAT_SESSIONS[number_to_reply]) > CHAT_HISTORY_LENGTH:
            CHAT_SESSIONS[number_to_reply].pop(0)

        if not (EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE):
            print("[EVOLUTION] Variáveis de ambiente ausentes. Encerrando thread.", file=sys.stderr)
            return 
        
        print("[PROCESSOR] Enviando resposta para Evolution API...", file=sys.stderr)
        url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
        payload = {"number": number_to_reply, "text": reply} 
        res = requests.post(url_send, json=payload, headers=headers, timeout=20)
        print(f"[EVOLUTION] {res.status_code} -> {res.text}", file=sys.stderr)
    except Exception as e:
        print(f"[PROCESSOR] ERRO FATAL no processamento assíncrono: {e}", file=sys.stderr)


@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    data = request.get_json(silent=True) or {}
    print(f"\n[DEBUG] Webhook Payload Recebido: {json.dumps(data)}", file=sys.stderr)
    threading.Thread(target=process_message, args=(data,)).start()
    return jsonify({"status": "processing_async"}), 200
