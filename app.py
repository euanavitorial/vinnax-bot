import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO PADRÃO ---
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
# -------------------------

# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# [MODELO COMPATÍVEL COM google-generativeai==0.8.5]
# "models/gemini-1.5-flash" é estável e 100% compatível.
GEMINI_MODEL_NAME = "models/gemini-1.5-flash"

# --- CONFIGURAÇÃO DO SUPABASE ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Monta a URL da API de clientes automaticamente
if SUPABASE_URL:
    base_url = SUPABASE_URL.rstrip("/")
    CLIENTE_API_ENDPOINT = f"{base_url}/functions/v1/api-clients"
else:
    CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "")

LANCAMENTO_API_ENDPOINT = os.environ.get("LANCAMENTO_API_ENDPOINT", "https://api.seusistema.com/v1/lancamentos")

app = Flask(__name__)

# ====== Memória e Deduplicação ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ====== Utilidades WhatsApp ======
def extract_text(message: Dict[str, Any]) -> str:
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message: return (message[mid].get("caption") or "").strip()
    return ""

def get_auth_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json"
    }


# ======================================================================
# FERRAMENTAS (TOOLS)
# ======================================================================
TOOLS_MENU = [
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
                "id_cliente": {"type": "INTEGER", "description": "O ID numérico do cliente."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "consultar_cliente_por_telefone",
        "description": "Busca os detalhes de um cliente usando o número de telefone.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "telefone": {"type": "STRING", "description": "Número com DDI, ex: 5511999999999."}
            },
            "required": ["telefone"]
        }
    },
    {
        "name": "atualizar_cliente",
        "description": "Modifica informações de um cliente existente usando o ID.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID do cliente."},
                "nome": {"type": "STRING", "description": "Novo nome (opcional)."},
                "telefone": {"type": "STRING", "description": "Novo telefone (opcional)."},
                "email": {"type": "STRING", "description": "Novo email (opcional)."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "excluir_cliente",
        "description": "Deleta um cliente do sistema usando o ID.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID do cliente a ser excluído."}
            },
            "required": ["id_cliente"]
        }
    }
]


# ======================================================================
# FUNÇÕES DA API
# ======================================================================
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}


def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao consultar cliente: {e}"}


def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        response = requests.post(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), json={"phone": telefone}, timeout=20)
        response.raise_for_status()
        dados = response.json()
        if isinstance(dados, list):
            if dados:
                return dados[0]
            return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone}."}
        return dados
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao consultar cliente por telefone: {e}"}


def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
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
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204:
            return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao excluir cliente: {e}"}


TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone,
    "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
}


# ====== Inicialização do Gemini ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)

        # Diagnóstico de modelos
        try:
            app.logger.info("=== MODELOS DISPONÍVEIS ===")
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    app.logger.info(f"✅ {m.name}")
            app.logger.info("============================")
        except Exception:
            pass

        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            safety_settings=safety_settings,
            tools=TOOLS_MENU
        )
        app.logger.info(f"[GEMINI] Modelo carregado: {GEMINI_MODEL_NAME}")

    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")
