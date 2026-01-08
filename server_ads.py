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