import os
import json
from collections import deque
from typing import Any, Dict
import threading 
import re 
import sys 
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ====== Config (Variáveis de Ambiente) ======
# Chaves da Evolution API (para enviar a resposta)
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

# --- CHAVES DO SUPABASE (Lovable) ---
# A URL da sua função principal de IA (do seu ajudante)
SUPABASE_PROCESS_URL = "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/process-with-ai"
# A chave "Anon" ou "Public" do seu Supabase (NÃO é a LOVABLE_API_KEY)
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# ====== Deduplicação (Anti-Loop) ======
PROCESSED_IDS = deque(maxlen=500)

# ====== Utilidades (No Topo) ======
def extract_text(message: Dict[str, Any]) -> str:
    """Extrai o texto de diversos tipos de mensagem do WhatsApp."""
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    # (Pode adicionar 'caption' de mídias se necessário no futuro)
    return ""

def normalize_phone_to_jid(phone: str) -> str:
    """Garante que o número esteja no formato JID para a Evolution API responder."""
    if not phone: return ""
    phone = re.sub(r'[^0-9]', '', phone) # Remove caracteres não numéricos
    if not phone.startswith("55"):
        phone = f"55{phone}"
    if not phone.endswith("@s.whatsapp.net"):
        phone = f"{phone}@s.whatsapp.net"
    return phone

# --- Processamento Assíncrono (O Trabalho Pesado) ---
def process_message(data):
    """Função que é executada em segundo plano para não dar timeout."""
    try:
        print("\n--- [PROCESSOR] Thread iniciada. ---", file=sys.stderr)
        
        envelope = data.get("data", data)
        if isinstance(envelope, list) and envelope: envelope = envelope[0]
        if not isinstance(envelope, dict): return 
        key = envelope.get("key", {}) or {}
        if key.get("fromMe") is True: return 
        message = envelope.get("message", {}) or {}
        if not message: return 
        
        # Deduplicação
        msg_id = key.get("id") or envelope.get("idMessage") or ""
        if msg_id and msg_id in PROCESSED_IDS: 
            print(f"[PROCESSOR] Ignorando mensagem duplicada: {msg_id}", file=sys.stderr)
            return 
        if msg_id: PROCESSED_IDS.append(msg_id)
        
        # 1. Extrair os dados (como sugerido pelo seu ajudante)
        jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
        phone_number = jid.split("@")[0] # Pega só o número (ex: 5564...)
        message_text = extract_text(message)
        contact_name = data.get("pushName", "")
        instance_name = data.get("instance", "VINNAXBEAUTY")

        # --- ✅ CORREÇÃO DO NAMEERROR ESTÁ AQUI ---
        if not message_text: # Era "if not text:", agora está correto
            print("[PROCESSOR] Texto vazio, encerrando thread.", file=sys.stderr)
            return 
        # --- FIM DA CORREÇÃO ---

        if not (SUPABASE_PROCESS_URL and SUPABASE_ANON_KEY):
            print("[PROCESSOR] ERRO FATAL: SUPABASE_PROCESS_URL ou SUPABASE_ANON_KEY não configuradas no Render.", file=sys.stderr)
            return

        # 2. Chamar a IA do Lovable/Supabase (process-with-ai)
        print(f"[PROCESSOR] Repassando para a IA do Supabase: {phone_number}...", file=sys.stderr)
        
        headers = {
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "phoneNumber": phone_number,
            "messageText": message_text,
            "contactName": contact_name,
            "instance": instance_name
        }
        
        response = requests.post(
            SUPABASE_PROCESS_URL,
            json=payload,
            headers=headers,
            timeout=30 # 30 segundos para a IA pensar
        )
        response.raise_for_status() # Lança erro se a IA do Supabase falhar
        
        ai_response = response.json()
        reply_text = ai_response.get("message", "Desculpe, não consegui processar sua resposta.")
        
        print(f"[PROCESSOR] Resposta da IA (Supabase) recebida: {reply_text[:50]}...", file=sys.stderr)

        # 3. Enviar a Resposta de volta via Evolution API
        if not (EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE):
            print("[EVOLUTION] Variáveis de ambiente ausentes. Encerrando thread.", file=sys.stderr)
            return 
        
        url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
        headers_evo = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
        payload_evo = {"number": jid, "text": reply_text} # Usamos o JID completo
        
        res = requests.post(url_send, json=payload_evo, headers=headers_evo, timeout=20)
        print(f"[EVOLUTION] {res.status_code} -> {res.text}", file=sys.stderr)

    except Exception as e:
        print(f"[PROCESSOR] ERRO FATAL no processamento assíncrono: {e}", file=sys.stderr)
        # Tenta enviar um erro para o usuário
        try:
            if 'jid' in locals() and jid: # Verifica se jid existe
                url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
                headers_evo = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
                payload_evo = {"number": jid, "text": "Desculpe, ocorreu um erro interno. Tente novamente."}
                requests.post(url_send, json=payload_evo, headers=headers_evo, timeout=20)
        except:
            pass # Falha silenciosa se não conseguir enviar o erro


# ====== Rotas (Apenas 2 rotas) ======
@app.route("/", methods=["GET"])
def home():
    """Rota simples para o Render verificar se o serviço está ativo."""
    return jsonify({"status": "ok", "service": "vinnax-bot-bridge"}), 200

@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    # Retorno imediato (200 OK) para evitar reenvio do Evolution
    data = request.get_json(silent=True) or {}
    
    print(f"\n[DEBUG] Webhook Payload Recebido: {json.dumps(data)}", file=sys.stderr)

    # Inicia o processamento pesado em segundo plano
    threading.Thread(target=process_message, args=(data,)).start()

    # Resposta imediata para evitar timeouts do Evolution
    return jsonify({"status": "processing_async"}), 200
