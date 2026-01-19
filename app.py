import os
import sys
import json
import asyncio
from datetime import datetime
from contextlib import AsyncExitStack

import chainlit as cl
from google import genai
from google.genai import types as gt

# Import de la librairie officielle (Fonctionne sur Linux/HF)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Config
MCP_SCRIPT_PATH = "server_ads.py"
GEMINI_MODEL = "gemini-2.5-flash"
CURRENT_DATE = datetime.now().strftime("%d %B %Y")

SYSTEM_INSTRUCTION = f"""
CONTEXTE : Date : {CURRENT_DATE}.
ROLE : Stratège Google Ads Senior.
RÈGLES :
- Dates : Utilise `DURING LAST_30_DAYS` par défaut.
- Argent : Divise les micros par 1 000 000.
"""

@cl.on_chat_start
async def start():
    # Sur Hugging Face, on utilise le python du système
    server_params = StdioServerParameters(
        command="python", # Pas besoin de sys.executable sur Docker
        args=[MCP_SCRIPT_PATH],
        env=os.environ
    )

    try:
        exit_stack = AsyncExitStack()
        
        # Connexion via la lib officielle (Stable sur Linux)
        read, write = await exit_stack.enter_async_context(stdio_client(server_params))
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        
        cl.user_session.set("exit_stack", exit_stack)
        cl.user_session.set("mcp_session", session)
        
        # Init Gemini
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        cl.user_session.set("gemini_client", client)
        cl.user_session.set("history", [])

        # Récupération des outils pour vérifier
        tools = await session.list_tools()
        tool_names = [t.name for t in tools.tools]
        
        await cl.Message(content=f"✅ **Agent Google Ads en ligne (Cloud)**\nOutils : `{', '.join(tool_names)}`").send()

    except Exception as e:
        await cl.Message(content=f"❌ Erreur démarrage : {e}").send()

@cl.on_message
async def main(message: cl.Message):
    session = cl.user_session.get("mcp_session")
    client = cl.user_session.get("gemini_client")
    history = cl.user_session.get("history")

    if not session:
        await cl.Message(content="⚠️ Session perdue.").send()
        return

    history.append(gt.Content(role="user", parts=[gt.Part(text=message.content)]))

    # Récupération live des outils
    mcp_tools = await session.list_tools()
    gemini_tools = [
        gt.Tool(function_declarations=[{
            "name": t.name,
            "description": t.description or "",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
        }]) for t in mcp_tools.tools
    ]

    msg = cl.Message(content="")
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=history,
            config=gt.GenerateContentConfig(temperature=0.3, tools=gemini_tools, system_instruction=SYSTEM_INSTRUCTION)
        )

        part = response.candidates[0].content.parts[0]

        if part.function_call:
            fc = part.function_call
            args = dict(fc.args)
            
            async with cl.Step(name=fc.name) as step:
                step.input = json.dumps(args, indent=2)
                # Appel standard
                result = await session.call_tool(fc.name, args)
                raw_data = result.content[0].text
                step.output = raw_data[:1000]
            
            prompt_suite = f"RÉSULTAT OUTIL:\n{raw_data}\n\nRéponds."
            final = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=history + [gt.Content(role="user", parts=[gt.Part(text=prompt_suite)])]
            )
            resp_text = final.text
            history.append(gt.Content(role="model", parts=[gt.Part(text=resp_text)]))
            msg.content = resp_text
        else:
            resp_text = part.text
            history.append(gt.Content(role="model", parts=[gt.Part(text=resp_text)]))
            msg.content = resp_text

        await msg.send()

    except Exception as e:
        await cl.Message(content=f"❌ Erreur : {e}").send()

@cl.on_chat_end
async def end():
    stack = cl.user_session.get("exit_stack")
    if stack: await stack.aclose()