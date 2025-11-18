import os
import json
import threading
import sys
import re
from collections import deque
from typing import Dict, Any, List

from flask import Flask, request, jsonify
import requests
import google.generativeai as genai
from google.api_core.exceptions import InvalidArgument

# ====== Configurações ======
app = Flask(__name__)

# Chaves do Render
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

# Configuração do Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Usando o modelo flash que é mais rápido e barato, ou pro se preferir
GEMINI_MODEL_NAME = "models/gemini-1.5-flash-latest" 

# Chave do seu Sistema (Lovable/Supabase)
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 

# Endpoints das suas APIs
CLIENTE_API_ENDPOINT = "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients"
PRODUTO_API_ENDPOINT = "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-products"
OS_API_ENDPOINT = "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-service-orders"
ORCAMENTO_API_ENDPOINT = "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-quotes"

# Memória e Controle
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[Dict]] = {} # Histórico compatível com Gemini SDK
CHAT_HISTORY_LENGTH = 20

# ====== Inicialização do Gemini ======
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("ERRO: GEMINI_API_KEY não encontrada.", file=sys.stderr)

# ====== Definição das Ferramentas (Tools) ======
# Definindo as ferramentas de forma que o SDK entenda nativamente
tools_lista = [
    # CLIENTE
    {"name": "criar_cliente", "description": "Cadastra cliente novo. O telefone é inserido automaticamente pelo sistema.", "parameters": {"type": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Verifica se o cliente já tem cadastro pelo telefone.", "parameters": {"type": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    # PRODUTO
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos e serviços disponíveis na gráfica.", "parameters": {"type": "OBJECT", "properties": {}}},
]

# ====== Funções Utilitárias ======
def get_headers():
    return {"x-api-key": LOVABLE_API_KEY, "Content-Type": "application/json"}

def normalize_phone(phone: str) -> str:
    """Remove caracteres e formata o telefone."""
    if not phone: return ""
    p = str(phone).replace("@s.whatsapp.net", "").strip()
    if p.startswith("55"): p = p[2:]
    # Remove o 9 extra ou 0 de operadora se necessário (lógica simplificada)
    if len(p) > 11 and p.startswith("0"): p = p[1:]
    return p

# ====== Funções de API (Ação Real) ======
def api_criar_cliente(nome, email=None, telefone=None):
    try:
        if not telefone: return {"erro": "Telefone obrigatório"}
        payload = {"name": nome, "phone": normalize_phone(telefone), "email": email}
        res = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_headers(), timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e: return {"erro": str(e)}

def api_consultar_cliente(telefone):
    try:
        tel_norm = normalize_phone(telefone)
        res = requests.get(CLIENTE_API_ENDPOINT, headers=get_headers(), timeout=10)
        res.raise_for_status()
        clientes = res.json()
        # Filtro manual simples
        for c in clientes:
            if normalize_phone(c.get("phone", "")) == tel_norm:
                return c
        return {"status": "nao_encontrado"}
    except Exception as e: return {"erro": str(e)}

def api_listar_produtos():
    try:
        res = requests.get(PRODUTO_API_ENDPOINT, headers=get_headers(), timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e: return {"erro": str(e)}

# Mapa de funções para execução
FUNCTION_MAP = {
    "criar_cliente": api_criar_cliente,
    "consultar_cliente_por_telefone": api_consultar_cliente,
    "consultar_produtos_todos": api_listar_produtos
}

# ====== Lógica do Chat ======
def chat_with_gemini(user_text, jid_completo):
    try:
        # 1. Prepara o histórico
        if jid_completo not in CHAT_SESSIONS:
            CHAT_SESSIONS[jid_completo] = []
        
        # 2. Cria o modelo com as ferramentas
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL_NAME,
            tools=tools_lista
            # REMOVIDO SAFETY_SETTINGS para evitar erro 400
        )
        
        # 3. Inicia o chat com histórico
        chat = model.start_chat(history=CHAT_SESSIONS[jid_completo])
        
        # 4. Envia mensagem do usuário
        # Adicionamos um contexto oculto sobre quem é o usuário
        telefone_usuario = normalize_phone(jid_completo)
        
        # Prompt do sistema injetado na mensagem (técnica segura)
        prompt_sistema = (
            f"[SISTEMA: O usuário tem o telefone {telefone_usuario}. "
            "Você é o assistente da Gráfica JB. "
            "Se o usuário disser o nome e não tiver cadastro, use a ferramenta `criar_cliente` imediatamente. "
            "O telefone será injetado automaticamente pela ferramenta, não peça o telefone.] "
            f"{user_text}"
        )
        
        response = chat.send_message(prompt_sistema)
        
        # 5. Verifica se a IA quer usar uma ferramenta (Function Call)
        # O SDK novo facilita isso. Ele processa a chamada automaticamente se configurado,
        # mas aqui vamos fazer manualmente para garantir a injeção do telefone.
        
        part = response.candidates[0].content.parts[0]
        
        if part.function_call:
            fname = part.function_call.name
            fargs = dict(part.function_call.args)
            
            print(f"[IA] Chamando ferramenta: {fname}", file=sys.stderr)
            
            # INJEÇÃO AUTOMÁTICA DE TELEFONE
            if fname in ["criar_cliente", "consultar_cliente_por_telefone"]:
                fargs["telefone"] = jid_completo # Passamos o JID, a função normaliza
            
            # Executa a função
            if fname in FUNCTION_MAP:
                api_result = FUNCTION_MAP[fname](**fargs)
                
                # Devolve o resultado para a IA gerar a resposta final
                # Na versão nova do SDK, enviamos o resultado como uma ToolResponse
                response = chat.send_message(
                    genai.protos.Content(
                        parts=[genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=fname,
                                response={"result": api_result}
                            )
                        )]
                    )
                )
        
        # 6. Pega o texto final
        resposta_final = response.text
        
        # Atualiza histórico local (limite de segurança)
        if len(CHAT_SESSIONS[jid_completo]) > 20:
            CHAT_SESSIONS[jid_completo] = CHAT_SESSIONS[jid_completo][-10:]
            
        return resposta_final

    except Exception as e:
        print(f"[IA ERRO] {e}", file=sys.stderr)
        return "Desculpe, tive um erro técnico rápido. Pode repetir?"

# ====== Processamento do Webhook ======
def process_webhook_data(data):
    try:
        # Extração segura de dados
        msg = data.get("data", {}).get("message", {})
        if not msg: return
        
        remote_jid = data.get("data", {}).get("key", {}).get("remoteJid", "")
        if not remote_jid or "status@broadcast" in remote_jid: return
        
        # Extrair texto
        user_text = (msg.get("conversation") or 
                     msg.get("extendedTextMessage", {}).get("text") or "").strip()
        
        if not user_text: return
        
        print(f"[MSG] Recebido de {remote_jid}: {user_text}", file=sys.stderr)
        
        # Chamar IA
        resposta = chat_with_gemini(user_text, remote_jid)
        
        # Enviar Resposta
        if EVOLUTION_URL_BASE and EVOLUTION_KEY:
            url = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
            payload = {"number": remote_jid, "text": resposta}
            headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
            requests.post(url, json=payload, headers=headers)
            print("[MSG] Resposta enviada.", file=sys.stderr)

    except Exception as e:
        print(f"[THREAD ERRO] {e}", file=sys.stderr)

@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook():
    # Responde rápido para não travar o WhatsApp
    data = request.get_json(silent=True) or {}
    
    # Filtra mensagens enviadas por mim mesmo
    if data.get("data", {}).get("key", {}).get("fromMe"):
        return jsonify({"status": "ignored"}), 200

    # Inicia thread
    threading.Thread(target=process_webhook_data, args=(data,)).start()
    return jsonify({"status": "queued"}), 200

@app.route("/", methods=["GET"])
def health():
    return "Bot Online", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
