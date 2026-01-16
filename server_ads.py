'''
from mcp.server.fastmcp import FastMCP
from google.ads.googleads.client import GoogleAdsClient
import os

mcp = FastMCP("Google Ads Local")

def get_client():
    # Charge la configuration depuis le JSON
    config = {
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        "login_customer_id": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
        "use_proto_plus": True
    }
    return GoogleAdsClient.load_from_dict(config)

@mcp.tool()
def search_google_ads(query: str) -> str:
    """
    Exécute une requête GAQL sur le compte Google Ads.
    Exemple: SELECT campaign.name, metrics.cost_micros FROM campaign LIMIT 5
    """
    try:
        client = get_client()
        ga_service = client.get_service("GoogleAdsService")
        # On nettoie l'ID (enlève les tirets si présents)
        customer_id = os.environ.get("GOOGLE_ADS_CUSTOMER_ID").replace("-", "")
        
        response = ga_service.search(customer_id=customer_id, query=query)
        
        # On formate les résultats en liste de strings pour l'IA
        results = []
        for row in response:
            results.append(str(row))
            
        if not results:
            return "Aucun résultat trouvé."
            
        return "\n---\n".join(results[:50]) # Limite à 50 pour la vitesse
    except Exception as e:
        return f"Erreur Ads: {str(e)}"

if __name__ == "__main__":
    mcp.run()
'''

import sys
import os
import traceback

# --- 1. MOUCHARD DE CRASH (Debug) ---
# Redirige les erreurs internes vers un fichier au lieu de casser le pipe
sys.stderr = open("server_error_log.txt", "w", buffering=1)

try:
    from mcp.server.fastmcp import FastMCP
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException

    # Initialisation du serveur
    mcp = FastMCP("Google Ads Local")

    # --- 2. CHARGEMENT CONFIG ROBUSTE ---
    def get_client():
        try:
            config = {
                "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
                "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
                "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
                "login_customer_id": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
                "use_proto_plus": True
            }
            # Vérification basique
            if not config["developer_token"]:
                return None
            return GoogleAdsClient.load_from_dict(config)
        except Exception as e:
            sys.stderr.write(f"Erreur init client: {e}\n")
            return None

    # Chargement global unique
    GLOBAL_CLIENT = get_client()

    @mcp.tool()
    def search_google_ads(query: str) -> str:
        """
        Exécute une requête GAQL. 
        Ne plante JAMAIS (renvoie l'erreur en texte).
        """
        # 1. Vérification Client
        if not GLOBAL_CLIENT:
            return "ERREUR CRITIQUE : Les clés API sont invalides ou manquantes dans l'environnement."

        try:
            ga_service = GLOBAL_CLIENT.get_service("GoogleAdsService")
            cust_id = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
            
            if not cust_id:
                return "ERREUR : Customer ID manquant."

            # 2. Exécution API (Le moment critique)
            # On utilise un itérateur pour éviter de charger trop de données en RAM d'un coup
            response = ga_service.search(customer_id=cust_id, query=query)
            
            results = []
            count = 0
            
            # 3. Lecture sécurisée
            for row in response:
                results.append(str(row))
                count += 1
                if count >= 50: # Limite de sécurité
                    break
            
            if not results:
                return "Requête valide, mais 0 résultat trouvé (vérifiez les dates ou statuts)."
                
            return "\n---\n".join(results)

        except GoogleAdsException as ex:
            # 4. Gestion des erreurs Google (Syntaxe SQL, Champs interdits...)
            error_msg = "ERREUR API GOOGLE ADS :\n"
            for error in ex.failure.errors:
                error_msg += f"- {error.message}\n"
            return error_msg

        except Exception as e:
            # 5. Gestion des crashs Python (Mémoire, Code...)
            # On renvoie l'erreur en texte pour que Gemini sache ce qu'il se passe
            return f"ERREUR TECHNIQUE INTERNE : {str(e)}"

    if __name__ == "__main__":
        mcp.run()

except Exception:
    # Si le script plante avant même de démarrer (Import, Syntaxe...)
    traceback.print_exc()