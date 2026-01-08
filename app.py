import os
import sys
import json
import asyncio
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

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
# 2. CERVEAU DU CONSULTANT (Stratège & Autonome)
# ======================
CURRENT_DATE = datetime.now().strftime("%d %B %Y")

SYSTEM_INSTRUCTION = f"""
CONTEXTE :
Date du jour : {CURRENT_DATE}.
Compte cible : {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}

TON RÔLE :
Tu es le **Stratège et Interprète** du compte Google Ads.
L'utilisateur te pose des questions métier. Ta mission est de trouver la preuve dans les données et d'expliquer ce qu'il se passe.

TA MÉTHODOLOGIE (LIBERTÉ TOTALE) :
1. **INVESTIGATION** : Utilise l'outil `search_google_ads` comme tu l'entends. Tu es libre de choisir les champs, les filtres et les périodes qui te semblent pertinents pour répondre.
2. **ADAPTATION** :
   - Si on demande "semaine dernière", "7 jours", "hier", tu es LIBRE d'utiliser les segments dynamiques (`LAST_7_DAYS`) ou de calculer les dates.
   - Si on demande "30 jours", utilise `LAST_30_DAYS`.
3. **INTERPRÉTATION (Crucial)** :
   - Ne te contente pas d'afficher un tableau de chiffres bruts.
   - **Raconte l'histoire** : "Le coût a augmenté car le CPC a doublé", "La campagne X porte tout le compte".
   - Convertis toujours les `cost_micros` en devise réelle (divisé par 1M).

CAS D'ERREUR :
Si l'outil renvoie "Aucun résultat", ne dis pas juste "Il n'y a rien". Propose une hypothèse (ex: "Campagne en pause ? Période trop courte ?").
"""

# ======================
# 3. STYLE & UI (Épuré)
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
# 4. HELPERS TECHNIQUES
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
        # Fallback Cloud (si chemin relatif)
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
# 6. CHARGEMENT ASYNC
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
        st.error(f"Erreur connexion MCP : {e}")
        return []

# ======================
# 7. MOTEUR DE CHAT (Boucle Silencieuse)
# ======================
async def run_agent_turn(user_q: str, client: genai.Client) -> str:
    # 1. Ajout message user
    st.session_state.messages.append(as_user(user_q))

    # Connexion MCP
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # Récupération des outils pour ce tour
            tl = await session.list_tools()
            tools_def = [
                gt.Tool(function_declarations=[{
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": {"type": "object", "properties": {}},
                }]) for t in tl.tools
            ]

            # 2. Boucle de réflexion (Max 5 étapes)
            for _ in range(5):
                
                # A. Gemini Réfléchit
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
                
                # B. Si Gemini répond (pas d'outil), c'est fini
                if not call:
                    text = extract_text(resp)
                    st.session_state.messages.append(as_model_text(text))
                    trim_history(st.session_state.messages)
                    return text

                # C. Exécution Outil (Silencieuse - Pas d'affichage UI)
                st.session_state.messages.append(as_model_call(call))
                
                args = dict(call.args or {})
                try:
                    result = await session.call_tool(call.name, args)
                    raw = result.content[0].text if result.content else "Aucune donnée."
                except Exception as e:
                    raw = f"Erreur outil: {str(e)}"
                
                # D. On renvoie le résultat à l'IA pour qu'elle continue
                st.session_state.messages.append(as_tool_resp(call.name, raw))
            
            return "Je n'ai pas réussi à conclure après plusieurs recherches."

# ======================
# 8. INTERFACE PRINCIPALE
# ======================

st.markdown(f"""
<div class="hero">
  <div class="brand">Google Ads Analyzer</div>
  <div style="color:#94a3b8; margin-top:4px; font-weight:600; font-size: 0.9em;">
    Pilotage Expert via Gemini Flash & MCP
  </div>
  <div class="chips" style="margin-top:10px;">
    <span>Date: {CURRENT_DATE}</span><span>Compte: {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}</span>
  </div>
</div>
""", unsafe_allow_html=True)

if "history" not in st.session_state: st.session_state.history = []
if "messages" not in st.session_state: st.session_state.messages = []

client = genai.Client(api_key=GEMINI_API_KEY)

# Zone Chat
st.markdown('<div class="glass">', unsafe_allow_html=True)

for role, msg in st.session_state.history:
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(msg)

user_q = st.chat_input("Ex: Audit des mots-clés les plus coûteux sans conversions ?")

if user_q:
    st.session_state.history.append(("user", user_q))
    with st.chat_message("user"):
        st.markdown(user_q)
        
    with st.spinner("Analyse expert..."):
        try:
            answer = asyncio.run(run_agent_turn(user_q, client))
        except Exception as e:
            answer = f"❌ Erreur critique : {e}"
            
    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)

st.markdown('</div>', unsafe_allow_html=True)