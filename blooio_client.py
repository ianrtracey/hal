import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://backend.blooio.com/v2/api"


class BlooioClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BLOOIO_API_KEY")
        if not self.api_key:
            raise ValueError("BLOOIO_API_KEY is required — set it in .env or pass it directly")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def check_auth(self) -> dict:
        """Verify authentication by calling /me."""
        resp = self.session.get(f"{BASE_URL}/me")
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    client = BlooioClient()
    try:
        me = client.check_auth()
        print("Authenticated successfully:", me)
    except requests.HTTPError as e:
        print(f"Auth failed ({e.response.status_code}): {e.response.text}")
