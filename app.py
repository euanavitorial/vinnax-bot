import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from google.generativeai.types.content_types import FunctionCall

# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest")

# --- SUAS APIS ---
# Chave da API principal do seu sistema (Autenticação x-api-key)
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 

# URL Base para a API de Clientes (Adicione esta ENV VAR no Render)
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients")
# URL Base para a API de Lançamentos (Ainda falta a doc)
LANCAMENTO_API_ENDPOINT = os.environ.get("LANCAMENTO_API_ENDPOINT", "https://api.seusistema.com/v1/lancamentos")


app = Flask(__name__)

# ====== Memória e Deduplicação ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (AGORA COM 4 DE CLIENTES)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE (4 FUNÇÕES) ---
    {
        "name": "criar_cliente",
        "description": "Cadastra um novo cliente no sistema. Requer nome e pelo menos telefone ou email.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "nome": {"type": "STRING", "description": "Nome completo do cliente."},
                "telefone": {"type": "STRING", "description": "Número de telefone do cliente (opcional)."},
                "email": {"type": "STRING", "description": "Endereço de email do cliente (opcional)."}
            },
            "required": ["nome"]
        }
    },
    {
        "name": "consultar_cliente_por_id",
        "description": "Busca os detalhes de um cliente (telefone, email, etc.) usando o ID do cliente.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "atualizar_cliente",
        "description": "Modifica informações de um cliente existente usando o ID do cliente. Use apenas os campos a serem alterados.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente a ser atualizado."},
                "nome": {"type": "STRING", "description": "Novo nome do cliente (opcional)."},
                "telefone": {"type": "STRING", "description": "Novo telefone do cliente (opcional)."},
                "email": {"type": "STRING", "description": "Novo email do cliente (opcional)."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "excluir_cliente",
        "description": "Deleta permanentemente um cliente do sistema usando o ID do cliente.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente a ser excluído."}
            },
            "required": ["id_cliente"]
        }
    }
    # ADICIONE AQUI AS FERRAMENTAS DE LANÇAMENTO FINANCEIRO, ESTOQUE, etc.
]


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (AGORA USANDO O CABEÇALHO CORRETO)
# ======================================================================
def get_auth_headers():
    """Retorna o cabeçalho de autenticação para as APIs do Lovable."""
    return {
        "x-api-key": LOVABLE_API_KEY,
        "Content-Type": "application/json"
    }

# --- FUNÇÕES DE CLIENTE ---

def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    """Ferramenta: Chama a API para criar um novo cliente (POST)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        # Limpa o payload de valores None (se telefone ou email não foram passados)
        payload = {k: v for k, v in payload.items() if v is not None} 
        
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}

def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    """Ferramenta: Chama a API para buscar cliente por ID (GET)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao consultar cliente: {e}"}

def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    """Ferramenta: Chama a API para atualizar um cliente (PUT)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao atualizar cliente: {e}"}

def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    """Ferramenta: Chama a API para deletar um cliente (DELETE)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        # APIs de DELETE geralmente retornam 204 (No Content), então verificamos o sucesso
        if response.status_code == 204:
            return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao excluir cliente: {e}"}
        
# !!! Adicione aqui as funções para as APIs de Lançamento Financeiro, Estoque, etc. !!!


# ======================================================================
# PASSO 3: O "ROTEADOR" (MAPA DE FERRAMENTAS)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    # Cliente
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
    # Lançamento Financeiro (Adicionar aqui)
    # Estoque (Adicionar aqui)
}

# ====== RESTO DO CÓDIGO (SEM ALTERAÇÃO) ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        from google import generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        safety_settings = {h: HarmBlockThreshold.BLOCK_NONE for h in HarmCategory}
        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME, safety_settings=safety_settings, tools=TOOLS_MENU
        )
        app.logger.info(f"[GEMINI] Modelo carregado com {len(TOOLS_MENU)} ferramentas.")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")

# ... (Funções answer_with_gemini, extract_text, rotas, etc. permanecem as mesmas)
# ... (Para manter a resposta concisa, assumo que o restante do código é o mesmo da última versão)
# ... (Por favor, use o código completo da mensagem anterior, e substitua APENAS as partes que modifiquei)
# ... (Como esta parte do código)

def answer_with_gemini(user_text: str, chat_history: List[str]) -> str:
    # ... (A lógica interna permanece a mesma do código completo anterior, mas agora ela usa o ROTEADOR)
    # ... (O código aqui é só para mostrar que a função usa o roteador)
    if not gemini_model: return f"Olá! Recebi sua mensagem: {user_text}"
    try:
        system_prompt = ("Você é um assistente da Vinnax Beauty. ... **FERRAMENTAS:** Use as ferramentas de cliente, lançamento financeiro, estoque, etc. Use as ferramentas disponíveis (`tools`) sempre que um cliente pedir uma ação relacionada a elas. Sempre confirme os dados antes de executar uma ação.")
        history_string = "\n".join(chat_history)
        full_prompt = (f"{system_prompt}\n\n=== Histórico da Conversa ===\n{history_string}\n=== Nova Mensagem ===\nCliente: {user_text}\nAtendente:")
        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        if candidate.finish_reason == "TOOL_USE":
            function_call: FunctionCall = candidate.content.parts[0].function_call
            tool_name = function_call.name
            tool_args = function_call.args
            if tool_name in TOOL_ROUTER:
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**dict(tool_args))
                tool_response_part = {"function_response": {"name": tool_name, "response": {"content": json.dumps(api_result)}}}
                response_final = gemini_model.generate_content([full_prompt, candidate.content, tool_response_part])
                txt = response_final.candidates[0].content.parts[0].text
            else: txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        else: txt = candidate.content.parts[0].text
        if not txt: return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."

# ... (O resto do código)

def webhook_messages_upsert():
    # ... (A lógica da rota webhook permanece a mesma)
    pass
