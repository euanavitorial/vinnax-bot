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

# --- IMPORTAÇÃO DO GEMINI ---
try:
    from google import generativeai as genai
    from google.generativeai import types
except ImportError:
    print("[ERRO FATAL] Biblioteca google.generativeai não encontrada.", file=sys.stderr)
    genai = None
    types = None

app = Flask(__name__)

# ====== Configurações ======
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = "models/gemini-1.5-pro-latest" # Ou gemini-2.0-flash se preferir

# Endpoints do seu sistema (Lovable/Supabase)
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "")
PRODUTO_API_ENDPOINT = os.environ.get("PRODUTO_API_ENDPOINT", "")
OS_API_ENDPOINT = os.environ.get("OS_API_ENDPOINT", "")
ORCAMENTO_API_ENDPOINT = os.environ.get("ORCAMENTO_API_ENDPOINT", "")

# Memória local (já que tiramos do Lovable, precisamos guardar aqui por enquanto)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 15

# ====== Utilidades ======
def get_auth_headers():
    return {"x-api-key": LOVABLE_API_KEY, "Content-Type": "application/json"}

def normalize_phone(phone: str) -> str:
    if not phone: return ""
    phone = str(phone).replace("@s.whatsapp.net", "").strip()
    if phone.startswith("55"): phone = phone[2:]
    if len(phone) >= 11 and phone[2] == "0": phone = phone[:2] + phone[3:]
    return phone

def log_api_error(e: Exception, function_name: str) -> Dict[str, Any]:
    error_msg = f"Erro em {function_name}: {str(e)}"
    print(f"[API_CALL] {error_msg}", file=sys.stderr)
    return {"status": "erro", "mensagem": "Houve um erro ao consultar o sistema."}

# ====== 20 FUNÇÕES DE API (As Ferramentas) ======
# (Mantendo as mesmas funções que já tínhamos validado)

def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    try:
        telefone_norm = normalize_phone(telefone) if telefone else None
        payload = {"name": nome, "phone": telefone_norm, "email": email}
        # Remove chaves vazias
        payload = {k: v for k, v in payload.items() if v}
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_cliente")

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    try:
        telefone_norm = normalize_phone(telefone)
        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        clientes = response.json()
        # Filtra localmente (idealmente sua API deveria filtrar, mas isso funciona)
        encontrado = next((c for c in clientes if c.get('phone') == telefone_norm), None)
        if encontrado: return encontrado
        return {"status": "nao_encontrado"}
    except Exception as e: return log_api_error(e, "consultar_cliente_por_telefone")

# ... (Para economizar espaço, assumo que as outras funções de Produto, OS e Orçamento 
# são idênticas às que já fizemos. Se precisar, eu colo todas as 20 novamente).
# Vou colocar um placeholder para a função de listar produtos para o teste funcionar:

def call_api_consultar_produtos_todos() -> Dict[str, Any]:
    try:
        response = requests.get(PRODUTO_API_ENDPOINT, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_produtos_todos")

# ====== Definição das Ferramentas para o Gemini ======
TOOLS_MENU = [
    {"name": "criar_cliente", "description": "Cadastra cliente. Requer nome. Telefone é automático.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Busca cliente pelo telefone.", "parameters": {"type_": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos e serviços disponíveis.", "parameters": {"type_": "OBJECT", "properties": {}}},
    # ... Adicione as outras 17 definições aqui se for usar tudo agora ...
]

TOOL_ROUTER = {
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone,
    "consultar_produtos_todos": call_api_consultar_produtos_todos
}

# ====== Inicialização do Gemini ======
gemini_model = None
if GEMINI_API_KEY and genai:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME, tools=TOOLS_MENU)
        print("[SISTEMA] Gemini carregado com sucesso.", file=sys.stderr)
    except Exception as e:
        print(f"[SISTEMA] Erro ao carregar Gemini: {e}", file=sys.stderr)

# ====== Lógica do Cérebro ======
def generate_response(user_message, phone_number, history):
    if not gemini_model: return "Erro: Cérebro IA offline."
    
    # Contexto Inicial: Buscar cliente
    initial_context = ""
    cliente = call_api_consultar_cliente_por_telefone(phone_number)
    
    if cliente.get("status") == "nao_encontrado":
        initial_context = f"[SISTEMA] Cliente novo. Telefone: {phone_number}. Ação: Pergunte o nome para cadastrar."
    elif "error" in cliente:
        initial_context = "[SISTEMA] Erro ao buscar cliente. Prossiga com cautela."
    else:
        nome = cliente.get("name", "Cliente")
        id_cliente = cliente.get("id")
        initial_context = f"[SISTEMA] Cliente identificado: {nome} (ID: {id_cliente}). Não pergunte dados já sabidos."

    # Prompt
    system_prompt = (
        "Você é o assistente da Gráfica JB Impressões. Seja cordial e eficiente.\n"
        "1. Se o cliente não for identificado, pegue o nome e use `criar_cliente`.\n"
        "2. Se pedir serviços, use `consultar_produtos_todos` e liste sem preços inicialmente.\n"
        "3. Responda em português, de forma humanizada."
    )

    history_str = "\n".join(history)
    full_prompt = f"{system_prompt}\n\n{initial_context}\n\nHistórico:\n{history_str}\n\nCliente: {user_message}\nAtendente:"

    try:
        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        # Lógica de Tool Calling
        if candidate.finish_reason == "TOOL_USE":
            call = candidate.content.parts[0].function_call
            fname = call.name
            args = dict(call.args)
            
            # Injeção de dependência (Telefone)
            if fname == "criar_cliente" and "telefone" not in args:
                args["telefone"] = phone_number
            
            if fname in TOOL_ROUTER:
                print(f"[IA] Chamando ferramenta: {fname}", file=sys.stderr)
                result = TOOL_ROUTER[fname](**args)
                
                # Retorno para a IA
                tool_response = {
                    "function_response": {
                        "name": fname,
                        "response": {"content": json.dumps(result)}
                    }
                }
                final_res = gemini_model.generate_content([full_prompt, candidate.content, tool_response])
                return final_res.candidates[0].content.parts[0].text.strip()
            
        return candidate.content.parts[0].text.strip()

    except Exception as e:
        print(f"[IA] Erro na geração: {e}", file=sys.stderr)
        return "Desculpe, tive um problema técnico momentâneo."

# ====== Rotas ======
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "brain"}), 200

# ROTA NOVA QUE O LOVABLE VAI CHAMAR
@app.route("/api/ai", methods=["POST"])
def api_ai_proxy():
    data = request.get_json(silent=True) or {}
    print(f"[DEBUG] Recebido do Lovable: {json.dumps(data)}", file=sys.stderr)
    
    # O Lovable provavelmente vai mandar algo como:
    # { "message": "Olá", "phone": "5564...", ... }
    # Vamos adaptar para garantir que pegamos o certo
    
    user_msg = data.get("message") or data.get("messageText") or ""
    phone = data.get("phone") or data.get("phoneNumber") or ""
    
    # Normalizar telefone vindo do Lovable se necessário
    phone = normalize_phone(phone)
    
    if not user_msg:
        return jsonify({"error": "No message provided"}), 400

    # Gerenciar histórico simples
    if phone not in CHAT_SESSIONS: CHAT_SESSIONS[phone] = []
    
    # Processar
    response_text = generate_response(user_msg, phone, CHAT_SESSIONS[phone])
    
    # Atualizar histórico
    CHAT_SESSIONS[phone].append(f"Cliente: {user_msg}")
    CHAT_SESSIONS[phone].append(f"Atendente: {response_text}")
    
    # Retornar JSON para o Lovable/Evolution enviar
    return jsonify({
        "message": response_text,
        "action": "reply" # Sinal para quem receber saber o que fazer
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
