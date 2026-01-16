import os
import sys
import json
import time
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional
import streamlit as st
from google import genai
from google.genai import types as gt

# ======================
# 1. CONFIGURATION SÉCURISÉE
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
# 2. LE CERVEAU DU CONSULTANT (VOTRE PROMPT EXACT)
# ======================
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

SYSTEM_INSTRUCTION = f"""
CONTEXTE :
Nous sommes le : {CURRENT_DATE} (Année-Mois-Jour).
Compte analysé : {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}

TON RÔLE :
Tu es un Stratège Google Ads Senior. Tu es autonome dans tes recherches et ton interprétation.

RÈGLES DE GESTION DES DATES (ALGORITHME) :
1. **Périodes Standards** : Si l'utilisateur demande une période standard, privilégie TOUJOURS les segments dynamiques :
   - `DURING LAST_30_DAYS`
   - `DURING LAST_7_DAYS`
   - `DURING THIS_MONTH`
   - `DURING LAST_MONTH`
   - `DURING YESTERDAY`

2. **Périodes Sur-Mesure** : Si la demande sort des standards, tu DOIS :
   - Calculer mentalement les dates 'YYYY-MM-DD' en te basant sur {CURRENT_DATE}.
   - Utiliser : `segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'`.

3. **Défaut** : Si aucune date n'est précisée, utilise `LAST_30_DAYS`.

4. **FOCUS ACTIVITÉ** : Ne regarde jamais les éléments (campagnes, groupes) qui sont en pause ou supprimés.

TA MÉTHODE DE RÉFLEXION :
Avant de faire ta requête SQL, demande-toi : "Quel est le niveau de détail demandé ?"
- Si c'est "Mot-clé", interroge la vue mot-clé (`keyword_view`) et le texte (`ad_group_criterion.keyword.text`).

RÈGLES D'ANALYSE :
- Divise toujours `metrics.cost_micros` par 1 000 000.
- Ne donne pas juste un tableau. Explique les chiffres.
"""

# ======================
# 3. GESTION DU SERVEUR (BAS NIVEAU / ANTI-CRASH) 🛠️
# ======================
# Cette section remplace la librairie 'mcp' qui causait l'erreur TaskGroup.
# Elle lance le script, fait le travail, et le ferme. C'est du béton.

def run_script_tool(tool_name: str, args: dict) -> str:
    """Lance le script serveur pour UNE exécution et le ferme."""
    
    # 1. Vérif fichier
    if not os.path.exists(MCP_SCRIPT_PATH):
        if os.path.exists("server_ads.py"):
            script_to_run = "server_ads.py"
        else:
            return f"ERREUR : Script introuvable ({MCP_SCRIPT_PATH})"
    else:
        script_to_run = MCP_SCRIPT_PATH

    # 2. Lancement du processus
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", script_to_run], # -u est vital
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **ADS_CREDENTIALS},
            bufsize=0
        )
    except Exception as e:
        return f"ERREUR LANCEMENT : {e}"

    try:
        # Fonctions internes de communication
        def send(msg):
            j = json.dumps(msg).encode('utf-8')
            proc.stdin.write(f"Content-Length: {len(j)}\r\n\r\n".encode('ascii') + j)
            proc.stdin.flush()

        def read():
            head = b""
            while b"\r\n\r\n" not in head:
                c = proc.stdout.read(1)
                if not c: return None
                head += c
            len_str = head.decode().split('Length:')[1].strip()
            return json.loads(proc.stdout.read(int(len_str)).decode())

        # 3. Dialogue MCP (Handshake)
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "manual"}, "capabilities": {}}})
        read() # On ignore la réponse d'init
        
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 4. Appel de l'outil
        send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call", 
            "params": {"name": tool_name, "arguments": args}
        })
        
        # 5. Lecture Résultat (Timeout 60s)
        # On utilise une boucle simple avec timeout
        start = time.time()
        response = None
        while time.time() - start < 60:
            if proc.poll() is not None: break # Le process est mort
            response = read()
            if response: break
        
        if response and "result" in response:
            return response["result"]["content"][0]["text"]
        elif response and "error" in response:
            return f"ERREUR OUTIL : {response['error']['message']}"
        
        return "Erreur : Le serveur n'a rien renvoyé (Timeout ou Crash)."

    except Exception as e:
        return f"ERREUR COMMUNICATION : {str(e)}"
    
    finally:
        # 6. Nettoyage violent (On tue le processus pour éviter les zombies)
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except:
            if proc.poll() is None:
                proc.kill()

# ======================
# 4. HELPERS UI & GEMINI
# ======================
def extract_text(resp) -> str:
    if getattr(resp, "text", None): return resp.text
    out = []
    for cand in getattr(resp, "candidates", []) or []:
        for part in getattr(cand.content, "parts", []) or []:
            if part.text: out.append(part.text)
    return "\n".join(out).strip()

def extract_tool_call(resp):
    for cand in getattr(resp, "candidates", []) or []:
        for part in getattr(cand.content, "parts", []) or []:
            if part.function_call: return part.function_call
    return None

# ======================
# 5. INTERFACE
# ======================
st.set_page_config(page_title="Ad's up — GAds Agent", page_icon="⚡", layout="wide")
st.markdown("""<style>
[data-testid="stHeader"] { background: transparent !important; }
.block-container { padding-top: 1rem !important; }
.glass { background: rgba(17, 24, 39, 0.7); border-radius: 16px; padding: 15px; border: 1px solid rgba(75, 85, 99, 0.4); }
.hero { padding: 18px; background: linear-gradient(135deg, #1e3a8a 0%, #111827 100%); border-radius: 16px; margin-bottom: 20px; }
</style>""", unsafe_allow_html=True)

st.markdown(f"""
<div class="hero">
  <h2>🤖 Google Ads Agent (Architecture Stable)</h2>
  <p style="margin-top:5px; opacity:0.8;">{CURRENT_DATE} • Compte {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}</p>
</div>
""", unsafe_allow_html=True)

# INIT SESSION
if "history" not in st.session_state: st.session_state.history = []
if "messages" not in st.session_state: st.session_state.messages = []

# CLIENT GEMINI
client = genai.Client(api_key=GEMINI_API_KEY)

# Déclaration manuelle de l'outil (Puisqu'on n'utilise pas la lib mcp pour lister)
TOOL_DEF = [gt.Tool(function_declarations=[{
    "name": "search_google_ads",
    "description": "Exécute une requête SQL GAQL sur Google Ads. Permet de récupérer coûts, conversions, etc.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "La requête GAQL (SELECT ... FROM ...)"}
        },
        "required": ["query"]
    }
}])]

# AFFICHAGE CHAT
st.markdown('<div class="glass">', unsafe_allow_html=True)
for msg in st.session_state.history:
    role = msg.get("role", "assistant")
    content = msg.get("content", "")
    with st.chat_message(role):
        st.markdown(content)

# INPUT UTILISATEUR
if user_q := st.chat_input("Ex: Coût des 2 derniers mois ?"):
    
    # 1. Sauvegarde User
    st.session_state.history.append({"role": "user", "content": user_q})
    # Conversion pour Gemini (Message User)
    st.session_state.messages.append(gt.Content(role="user", parts=[gt.Part(text=user_q)]))
    
    with st.chat_message("user"):
        st.markdown(user_q)
    
    # 2. Moteur de Réflexion (Boucle Agentique)
    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("⏳ *Analyse...*")
        
        final_text = ""
        
        # On autorise jusqu'à 5 tours de réflexion (Agent)
        for _ in range(5):
            try:
                # A. Appel Gemini
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=st.session_state.messages,
                    config=gt.GenerateContentConfig(
                        temperature=0.3,
                        tools=TOOL_DEF,
                        system_instruction=SYSTEM_INSTRUCTION
                    )
                )
                
                # B. Analyse réponse
                call = extract_tool_call(response)
                
                # Si réponse texte finale -> FIN
                if not call:
                    final_text = extract_text(response)
                    st.session_state.messages.append(gt.Content(role="model", parts=[gt.Part(text=final_text)]))
                    break 

                # Si appel d'outil -> EXÉCUTION
                st.session_state.messages.append(gt.Content(role="model", parts=[gt.Part(function_call=call)]))
                
                tool_name = call.name
                args = dict(call.args)
                
                # UI Debug
                with st.expander(f"🛠️ Exécution : {tool_name}", expanded=False):
                    st.code(args.get("query", str(args)), language="sql")
                
                # C. Exécution Low-Level (Le truc qui ne plante pas)
                tool_result = run_script_tool(tool_name, args)
                
                # UI Result
                with st.expander("📊 Résultat brut", expanded=False):
                    st.text(tool_result[:1000])
                
                # D. Feedback à Gemini
                st.session_state.messages.append(gt.Content(role="tool", parts=[gt.Part(function_response={"name": tool_name, "response": {"result": tool_result}})]))
                
                # ... et la boucle continue !

            except Exception as e:
                final_text = f"❌ Erreur : {str(e)}"
                break
        
        # Affichage final
        placeholder.markdown(final_text)
        st.session_state.history.append({"role": "assistant", "content": final_text})

st.markdown('</div>', unsafe_allow_html=True)