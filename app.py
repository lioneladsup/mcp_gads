import os
import sys
import json
import asyncio
import subprocess
from typing import Any, Dict, List, Optional

import streamlit as st
from google import genai
from google.genai import types as gt
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

# ======================
# 1. CHARGEMENT SÉCURISÉ DES SECRETS
# ======================
try:
    # Récupération des clés depuis secrets.toml
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    MCP_SCRIPT_PATH = st.secrets["script_path"]
    
    # Construction du dictionnaire pour l'environnement du script
    # On mappe les clés du TOML vers les variables d'environnement attendues par server_ads.py
    ADS_CREDENTIALS = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": st.secrets["google_ads"]["developer_token"],
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": st.secrets["google_ads"]["login_customer_id"],
        "GOOGLE_ADS_REFRESH_TOKEN": st.secrets["google_ads"]["refresh_token"],
        "GOOGLE_ADS_CLIENT_ID": st.secrets["google_ads"]["client_id"],
        "GOOGLE_ADS_CLIENT_SECRET": st.secrets["google_ads"]["client_secret"],
        "GOOGLE_ADS_CUSTOMER_ID": st.secrets["google_ads"]["customer_id"]
    }
    
    GEMINI_MODEL = "gemini-2.5-flash"

except FileNotFoundError:
    st.error("❌ Fichier `.streamlit/secrets.toml` introuvable.")
    st.stop()
except KeyError as e:
    st.error(f"❌ Clé manquante dans secrets.toml : {e}")
    st.stop()

# ======================
# 2. STYLE & CONFIGURATION
# ======================
st.set_page_config(page_title="Ad's up — GAds Chat", page_icon="⚡", layout="wide")
st.markdown("""
<style>
/* Style épuré sans header blanc */
[data-testid="stHeader"] { background: transparent !important; }
.block-container { padding-top: 1rem !important; }

/* Hero Banner */
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

/* Zone Chat */
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
# 3. HELPERS TECHNIQUES
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
# 4. CONFIGURATION MCP
# ======================
def _build_server_params() -> StdioServerParameters:
    if not os.path.exists(MCP_SCRIPT_PATH):
        st.error(f"❌ Script introuvable : `{MCP_SCRIPT_PATH}`")
        st.info("💡 Conseil : Vérifiez le chemin dans secrets.toml > [paths] > script_path")
        st.stop()
        
    return StdioServerParameters(
        command=sys.executable, 
        args=["-u", MCP_SCRIPT_PATH], 
        env=ADS_CREDENTIALS, # Les clés chargées depuis secrets.toml
    )

SERVER_PARAMS = _build_server_params()

# ======================
# 5. CHARGEMENT ASYNC DES OUTILS
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
# 6. MOTEUR DE CHAT (Un Tour)
# ======================
async def run_one_turn(user_q: str, tools: List[gt.Tool], client: genai.Client) -> str:
    st.session_state.messages.append(as_user(user_q))

    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Appel Gemini avec Outils
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=st.session_state.messages,
                config=gt.GenerateContentConfig(temperature=0.3, tools=tools),
            )

            call = extract_tool_call(resp)
            
            # Pas d'outil ? On renvoie le texte direct
            if not call:
                text = extract_text(resp) or "(pas de réponse)"
                st.session_state.messages.append(as_model_text(text))
                trim_history(st.session_state.messages)
                return text

            # 2. Exécution Outil (Silencieuse)
            st.session_state.messages.append(as_model_call(call))
            # (Pas d'affichage "Exécution en cours" ici)

            args = dict(call.args or {})
            result = await session.call_tool(call.name, args)
            raw = result.content[0].text if result.content else "{}"
            
            st.session_state.messages.append(as_tool_resp(call.name, raw))

            # 3. Synthèse Finale
            resp2 = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=st.session_state.messages,
                config=gt.GenerateContentConfig(temperature=0.7),
            )
            final_text = extract_text(resp2) or "(aucun texte)"
            st.session_state.messages.append(as_model_text(final_text))
            trim_history(st.session_state.messages)
            return final_text

# ======================
# 7. INTERFACE PRINCIPALE
# ======================

# Header
st.markdown(f"""
<div class="hero">
  <div class="brand">Google Ads Analyzer</div>
  <div style="color:#94a3b8; margin-top:4px; font-weight:600; font-size: 0.9em;">
    Pilotage de campagnes via Gemini Flash & MCP Local
  </div>
  <div class="chips" style="margin-top:10px;">
    <span>Gemini 2.5</span><span>Compte: {ADS_CREDENTIALS['GOOGLE_ADS_CUSTOMER_ID']}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# Initialisation Session
if "history" not in st.session_state: st.session_state.history = []
if "messages" not in st.session_state: st.session_state.messages = []

# Client Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Chargement Outils (Cache)
@st.cache_resource
def _load_tools():
    return asyncio.run(list_mcp_tools())

try:
    tools = _load_tools()
except Exception as e:
    st.error(f"Impossible de charger les outils : {e}")
    st.stop()

# Zone Chat
st.markdown('<div class="glass">', unsafe_allow_html=True)

# Affichage des messages passés
for role, msg in st.session_state.history:
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(msg)

# Input
user_q = st.chat_input("Ex: Coûts et Conversions des campagnes PMAX sur 30 jours ?")

if user_q:
    # User message
    st.session_state.history.append(("user", user_q))
    with st.chat_message("user"):
        st.markdown(user_q)
        
    # Assistant response
    with st.spinner("Analyse..."):
        try:
            answer = asyncio.run(run_one_turn(user_q, tools, client))
        except Exception as e:
            answer = f"❌ Erreur : {e}"
            
    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)

st.markdown('</div>', unsafe_allow_html=True)