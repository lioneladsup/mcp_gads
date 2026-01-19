import sys
import os
import traceback
from mcp.server.fastmcp import FastMCP
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# Redirection logs vers stderr (Standard sous Linux)
sys.stderr = sys.stdout 

mcp = FastMCP("Google Ads Cloud")

def get_client():
    config = {
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        "login_customer_id": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
        "use_proto_plus": True
    }
    return GoogleAdsClient.load_from_dict(config)

GLOBAL_CLIENT = None

@mcp.tool()
def search_google_ads(query: str) -> str:
    global GLOBAL_CLIENT
    try:
        if not GLOBAL_CLIENT: GLOBAL_CLIENT = get_client()
        
        ga_service = GLOBAL_CLIENT.get_service("GoogleAdsService")
        cust_id = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
        
        response = ga_service.search(customer_id=cust_id, query=query)
        
        results = []
        count = 0
        for row in response:
            results.append(str(row))
            count += 1
            if count >= 50: break
        
        if not results: return "0 résultat."
        return "\n---\n".join(results)

    except GoogleAdsException as ex:
        errs = [e.message for e in ex.failure.errors]
        return f"ERREUR API : {'; '.join(errs)}"
    except Exception as e:
        return f"ERREUR : {str(e)}"

if __name__ == "__main__":
    mcp.run()