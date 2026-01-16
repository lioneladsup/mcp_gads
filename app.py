import os
import sys
import json
import time
import subprocess
import re
from datetime import datetime
import streamlit as st
import google.generativeai as genai
from google.genai import types as gt

# ======================
# 1. CONFIGURATION
# ======================
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    MCP_SCRIPT_PATH = st.secrets["script_path"]
    
    ADS_CREDENTIALS = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": st.secrets["google_ads"]["developer_token"],
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": st.secrets["google_ads"]["login_customer_id"],
        "GOOGLE_ADS_REFRESH_TOKEN": st.secrets["google_ads"]["refresh_token"],
        "GOOGLE_ADS_CLIENT_ID": st.secrets["google_ads"]["client_id"],
        "GOOGLE_ADS_CLIENT_SECRET": st.secrets["google_ads"]["client_secret"],
        "GOOGLE_ADS_CUSTOMER_ID": st.secrets["google_ads"]["customer_id"]
    }
    GEMINI_MODEL = "gemini-2.5-flash"

except (FileNotFoundError, KeyError):
    st.error("❌ ERREUR SÉCURITÉ : Secrets introuvables.")
    st.stop()

# ======================
# 2. CERVEAU (Prompt)
# ======================
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

SYSTEM_INSTRUCTION = f"""
CONTEXTE : Date : {CURRENT_DATE}. Compte : {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}
ROLE : Stratège Google Ads Senior.

RÈGLES D'OR :
1. DATES : `DURING LAST_30_DAYS` par défaut.
2. ARGENT : Divise `cost_micros` par 1 000 000.
3. FILTRES : `status='ENABLED'` et `metrics.impressions > 0`.
4. MAPPING : Mot-clé -> `ad_group_criterion.keyword.text`.
"""

# ======================
# 3. GESTION DU SERVEUR "ONE-SHOT" 🛠️
# ======================
# Cette section gère la communication bas niveau sans librairie complexe.
# Elle lance le serveur, fait la requête, et coupe tout. C'est INCASSABLE.

def send_rpc(proc, payload):
    """Envoie une commande JSON au processus."""
    msg = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    header = f"Content-Length: {len(msg)}\r\n\r\n".encode('ascii')
    proc.stdin.write(header + msg)
    proc.stdin.flush()

def read_rpc(proc):
    """Lit une réponse JSON du processus."""
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = proc.stdout.read(1)
        if not chunk: return None # Fin de flux
        header += chunk
    
    content_length = 0
    header_str = header.decode('ascii', errors='ignore')
    for line in header_str.split('\r\n'):
        if line.lower().startswith('content-length:'):
            content_length = int(line.split(':')[1].strip())
    
    if content_length == 0: return None
    body = proc.stdout.read(content_length)
    return json.loads(body.decode('utf-8'))

def execute_tool_fresh(tool_name, args):
    """
    STRATÉGIE STABLE :
    1. Lance un nouveau serveur frais.
    2. Exécute la commande.
    3. Tue le serveur immédiatement.
    """
    
    # 1. Lancement propre
    proc = subprocess.Popen(
        [sys.executable, "-u", MCP_SCRIPT_PATH], # -u est vital
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=ADS_CREDENTIALS,
        bufsize=0
    )

    try:
        # 2. Initialisation (Handshake MCP obligatoire)
        init_req = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "client"}, "capabilities": {}}
        }
        send_rpc(proc, init_req)
        read_rpc(proc) # On ignore la réponse d'init
        
        send_rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. Appel de l'outil
        call_req = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool_name, "arguments": args}
        }
        send_rpc(proc, call_req)
        
        # 4. Résultat
        resp = read_rpc(proc)
        
        if resp and "result" in resp:
            return resp["result"]["content"][0]["text"]
        elif resp and "error" in resp:
            return f"ERREUR OUTIL : {resp['error']['message']}"
        return "Erreur : Réponse vide."

    except Exception as e:
        return f"ERREUR CRITIQUE : {str(e)}"
    
    finally:
        # 5. NETTOYAGE TOTAL (Kill)
        # On s'assure que rien ne traîne en mémoire
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except:
            proc.kill()

# ======================
# 4. MOTEUR AGENTIQUE
# ======================
def get_gemini_response(history_list, prompt=None):
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Définition manuelle de l'outil pour Gemini
    tools = [gt.Tool(function_declarations=[{
        "name": "search_google_ads",
        "description": "Exécute une requête SQL GAQL sur Google Ads.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "La requête GAQL"}
            },
            "required": ["query"]
        }
    }])]

    # Conversion historique Streamlit -> Gemini
    contents = []
    for msg in history_list:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(gt.Content(role=role, parts=[gt.Part(text=msg["content"])]))
    
    if prompt:
        contents.append(gt.Content(role="user", parts=[gt.Part(text=prompt)]))

    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=gt.GenerateContentConfig(
            temperature=0.3,
            tools=tools,
            system_instruction=SYSTEM_INSTRUCTION
        )
    )

# ======================
# 5. INTERFACE
# ======================
st.set_page_config(page_title="Ad's up — GAds Agent", page_icon="⚡", layout="wide")
st.markdown("""<style>
[data-testid="stHeader"] { background: transparent !important; }
.block-container { padding-top: 1rem !important; }
.glass { background: rgba(17, 24, 39, 0.7); border-radius: 16px; padding: 15px; border: 1px solid rgba(75, 85, 99, 0.4); }
</style>""", unsafe_allow_html=True)

st.markdown(f"""
<div style="padding:20px; background:linear-gradient(135deg, #1e3a8a 0%, #111827 100%); border-radius:15px; color:white; margin-bottom:20px;">
  <h2 style="margin:0;">🤖 Google Ads Agent (One-Shot)</h2>
  <p style="margin:5px 0 0 0; opacity:0.8;">{CURRENT_DATE} • Compte {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}</p>
</div>
""", unsafe_allow_html=True)

if "history" not in st.session_state: st.session_state.history = []

# Affichage Historique
st.markdown('<div class="glass">', unsafe_allow_html=True)
for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input Utilisateur
if user_q := st.chat_input("Ex: Coût des 2 derniers mois ?"):
    
    # 1. Afficher User
    st.session_state.history.append({"role": "user", "content": user_q})
    with st.chat_message("user"):
        st.markdown(user_q)
    
    # 2. Traitement
    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("⏳ *Analyse...*")
        
        try:
            # A. Premier Appel Gemini
            response = get_gemini_response(st.session_state.history)
            final_text = ""
            
            # Cas 1 : Réponse texte directe
            if response.text:
                final_text = response.text
                
            # Cas 2 : Demande d'outil (Function Call)
            elif response.candidates[0].content.parts[0].function_call:
                fc = response.candidates[0].content.parts[0].function_call
                tool_name = fc.name
                args = dict(fc.args)
                
                # Debug
                with st.expander(f"🛠️ Exécution : {tool_name}", expanded=False):
                    st.code(args.get("query", str(args)), language="sql")
                
                # C. EXÉCUTION "ONE-SHOT" (Stable)
                # On lance le serveur, on prend l'info, on tue le serveur.
                tool_result = execute_tool_fresh(tool_name, args)
                
                # Debug Résultat
                with st.expander("📊 Résultat brut", expanded=False):
                    st.text(tool_result[:1000])
                
                # D. Second Appel Gemini (Synthèse)
                # On triche en injectant le résultat comme si c'était l'utilisateur qui donnait la data
                # C'est plus simple que de gérer l'historique function_call complexe
                next_prompt = f"""
                RÉSULTAT DE LA REQUÊTE :
                {tool_result}
                
                ANALYSE ET RÉPONDS À L'UTILISATEUR MAINTENANT.
                """
                final_resp = get_gemini_response(st.session_state.history, prompt=next_prompt)
                final_text = final_resp.text

            # Affichage Final
            placeholder.markdown(final_text)
            st.session_state.history.append({"role": "assistant", "content": final_text})

        except Exception as e:
            err = f"❌ Erreur : {str(e)}"
            placeholder.error(err)
            st.session_state.history.append({"role": "assistant", "content": err})

st.markdown('</div>', unsafe_allow_html=True)