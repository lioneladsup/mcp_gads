import os
import sys
import json
import asyncio
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import re

import streamlit as st
from google import genai
from google.genai import types as gt
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

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
    st.error("❌ ERREUR SÉCURITÉ : Fichier secrets.toml introuvable ou incomplet.")
    st.stop()

# ======================
# 2. CERVEAU "SMART DATES" (VOTRE PROMPT DE BASE)
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

2. **Périodes Sur-Mesure** : Si la demande sort des standards (ex: "les 3 derniers mois", "depuis lundi", "du 1er au 15 janv"), tu DOIS :
   - Calculer mentalement les dates de début et de fin précises au format 'YYYY-MM-DD' en te basant sur la date du jour ({CURRENT_DATE}).
   - Utiliser la syntaxe : `segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'`.

3. **Défaut** : Si aucune date n'est précisée, utilise `LAST_30_DAYS`.

4. **FOCUS ACTIVITÉ** : Ne regarde jamais les éléments (campagnes, groupes, pubs) qui sont en pause, supprimés ou qui ne diffusent pas.

TA MÉTHODE DE RÉFLEXION :
Avant de faire ta requête SQL, demande-toi : "Quel est le niveau de détail demandé ?"
- Si c'est "Mot-clé", ta requête SQL **DOIT** interroger la vue mot-clé pour obtenir le texte exact du mot, pas juste le nom du groupe parent.

RÈGLES D'ANALYSE :
- Divise toujours `metrics.cost_micros` par 1 000 000.
- Ne donne pas juste un tableau. **Explique** les chiffres. Cherche les causes (CPC ? CTR ?).
- Si une requête renvoie 0, vérifie tes dates. Si les dates sont bonnes, c'est que le compte n'a pas diffusé. Dis-le clairement.
"""

# ======================
# 3. STYLE & UI
# ======================
st.set_page_config(page_title="Ad's up — GAds Agent", page_icon="⚡", layout="wide")
st.markdown("""
<style>
[data-testid="stHeader"] { background: transparent !important; }
.block-container { padding-top: 1rem !important; }
.hero {
  border: 1px solid #374151; border-radius: 16px; padding: 18px 18px;
  background: linear-gradient(135deg, #1e3a8a 0%, #0f172a 35%, #111827 100%);
  margin-bottom: 20px;
}
.brand {
  font-size: 26px; font-weight: 900; letter-spacing: -0.02em;
  background: linear-gradient(90deg, #60a5fa 0%, #34d399 50%, #facc15 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.chips span{
  display:inline-block; padding:4px 10px; border-radius:999px;
  background:#1f2937; border:1px solid #374151; font-size:12px; color:#e5e7eb; margin-right:6px;
}
.glass {
  background: rgba(17, 24, 39, 0.7);
  border: 1px solid rgba(75, 85, 99, 0.4);
  border-radius: 16px; padding: 15px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.5);
}
.stChatMessage { background-color: transparent !important; border: none !important; }
</style>
""", unsafe_allow_html=True)

# ======================
# 4. HELPERS
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

def as_user(text: str) -> gt.Content:
    return gt.Content(role="user", parts=[gt.Part(text=text)])

def as_model_text(text: str) -> gt.Content:
    return gt.Content(role="model", parts=[gt.Part(text=text)])

def as_model_call(call) -> gt.Content:
    return gt.Content(role="model", parts=[gt.Part(function_call=call)])

def as_tool_resp(name: str, resp) -> gt.Content:
    if not isinstance(resp, dict): resp = {"raw": str(resp)}
    return gt.Content(role="tool", parts=[gt.Part(function_response={"name": name, "response": resp})])

def trim_history(msgs: List[gt.Content], keep_last: int = 20):
    if len(msgs) > keep_last: del msgs[:-keep_last]

# ======================
# 5. CONFIGURATION MCP
# ======================
def _build_server_params() -> StdioServerParameters:
    if not os.path.exists(MCP_SCRIPT_PATH):
        if os.path.exists("server_ads.py"):
             return StdioServerParameters(command=sys.executable, args=["-u", "server_ads.py"], env=ADS_CREDENTIALS)
        st.error(f"❌ Script introuvable : `{MCP_SCRIPT_PATH}`")
        st.stop()
        
    return StdioServerParameters(
        command=sys.executable, 
        args=["-u", MCP_SCRIPT_PATH], 
        env=ADS_CREDENTIALS,
    )

SERVER_PARAMS = _build_server_params()

# ======================
# 6. CHARGEMENT OUTILS
# ======================
async def list_mcp_tools() -> List[gt.Tool]:
    try:
        async with stdio_client(SERVER_PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tl = await session.list_tools()
                tools = [
                    gt.Tool(function_declarations=[{
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": {"type": "object", "properties": {}},
                    }])
                    for t in tl.tools
                ]
                return tools
    except Exception as e:
        return []

# ======================
# 7. MOTEUR AGENTIQUE (AUTO-RESTART INTÉGRÉ) 🔄
# ======================
async def run_agent_turn(user_q: str, client: genai.Client) -> str:
    st.session_state.messages.append(as_user(user_q))

    # --- MÉCANISME DE REDÉMARRAGE AUTOMATIQUE ---
    MAX_RETRIES = 3
    
    for attempt in range(MAX_RETRIES):
        try:
            # On crée une nouvelle connexion à chaque tentative
            async with stdio_client(SERVER_PARAMS) as (read, write):
                async with ClientSession(read, write) as session:
                    
                    await session.initialize()
                    tl = await session.list_tools()
                    
                    tools_def = [
                        gt.Tool(function_declarations=[{
                            "name": t.name,
                            "description": t.description or "",
                            "parameters": {"type": "object", "properties": {}},
                        }]) for t in tl.tools
                    ]

                    # Boucle de réflexion (5 étapes max)
                    for _ in range(5):
                        
                        resp = client.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=st.session_state.messages,
                            config=gt.GenerateContentConfig(
                                temperature=0.3, 
                                tools=tools_def,
                                system_instruction=SYSTEM_INSTRUCTION
                            ),
                        )

                        call = extract_tool_call(resp)
                        
                        if not call:
                            text = extract_text(resp)
                            st.session_state.messages.append(as_model_text(text))
                            trim_history(st.session_state.messages)
                            return text

                        # --- PAS DE DOUBLE CHECK (Direct Exécution) ---
                        st.session_state.messages.append(as_model_call(call))
                        
                        # Debug UI
                        args = dict(call.args or {})
                        with st.chat_message("assistant"):
                            with st.expander(f"🛠️ Debug Requête : {call.name}", expanded=False):
                                if "query" in args: st.code(args["query"], language="sql")
                                else: st.json(args)
                        
                        # Appel réel avec Timeout
                        try:
                            result = await asyncio.wait_for(session.call_tool(call.name, args), timeout=60.0)
                            raw = result.content[0].text if result.content else "Aucune donnée."
                        except asyncio.TimeoutError:
                            raw = "ERREUR TIMEOUT : La requête est trop lourde."
                        except Exception as e:
                            raw = f"ERREUR OUTIL : {str(e)}"
                        
                        # Debug Résultat
                        with st.chat_message("assistant"):
                            with st.expander("📊 Debug Réponse brute", expanded=False):
                                st.text(raw[:1000] + "..." if len(raw) > 1000 else raw)

                        st.session_state.messages.append(as_tool_resp(call.name, raw))
                    
                    return "J'ai atteint la limite de mes recherches."

        except Exception as e:
            # === GESTION DE L'ERREUR TASKGROUP (AUTO-REPAIR) ===
            if attempt < MAX_RETRIES - 1:
                st.toast(f"⚠️ Instabilité serveur (Tentative {attempt+1}/{MAX_RETRIES}). Redémarrage...", icon="🔄")
                await asyncio.sleep(2) # On laisse le temps au système de nettoyer le port
                continue # On relance la boucle, ce qui relance le processus Python
            else:
                return f"❌ Erreur critique persistante après {MAX_RETRIES} essais : {str(e)}"

# ======================
# 8. INTERFACE
# ======================

st.markdown(f"""
<div class="hero">
  <div class="brand">Google Ads Analyzer</div>
  <div style="color:#94a3b8; margin-top:4px; font-weight:600; font-size: 0.9em;">
    Pilotage Expert via Gemini Flash & MCP
  </div>
  <div class="chips" style="margin-top:10px;">
    <span>Date: {datetime.now().strftime('%d/%m/%Y')}</span><span>Compte: {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}</span>
  </div>
</div>
""", unsafe_allow_html=True)

if "history" not in st.session_state: st.session_state.history = []
if "messages" not in st.session_state: st.session_state.messages = []

client = genai.Client(api_key=GEMINI_API_KEY)

@st.cache_resource
def _load_tools():
    return asyncio.run(list_mcp_tools())

try:
    tools = _load_tools()
except Exception as e:
    pass

st.markdown('<div class="glass">', unsafe_allow_html=True)

for role, msg in st.session_state.history:
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(msg)

user_q = st.chat_input("Ex: Coût des 2 derniers mois ? Pourquoi ça baisse ?")

if user_q:
    st.session_state.history.append(("user", user_q))
    with st.chat_message("user"):
        st.markdown(user_q)
        
    with st.spinner("Analyse expert..."):
        try:
            answer = asyncio.run(run_agent_turn(user_q, client))
        except Exception as e:
            answer = f"❌ Erreur irrécupérable : {e}"
            
    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)

st.markdown('</div>', unsafe_allow_html=True)