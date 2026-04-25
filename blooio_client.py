import os
from contextlib import contextmanager
from urllib.parse import quote

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

    def _chat_url(self, chat_id: str) -> str:
        return f"{BASE_URL}/chats/{quote(chat_id, safe='')}"

    def _number_url(self, phone_number: str) -> str:
        return f"{BASE_URL}/me/numbers/{quote(phone_number, safe='')}"

    def send_message(self, chat_id: str, text: str, *, share_contact: bool = False) -> dict:
        """Send a message to a chat (phone number or email)."""
        body: dict = {"text": text}
        if share_contact:
            body["share_contact"] = True
        resp = self.session.post(
            f"{self._chat_url(chat_id)}/messages",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    def list_numbers(self) -> dict:
        """List all phone numbers associated with this API key."""
        resp = self.session.get(f"{BASE_URL}/me/numbers")
        resp.raise_for_status()
        return resp.json()

    def get_contact_card(self, phone_number: str) -> dict:
        """Get the contact card (name, avatar, sharing settings) for a number."""
        resp = self.session.get(f"{self._number_url(phone_number)}/contact-card")
        resp.raise_for_status()
        return resp.json()

    def update_contact_card(
        self,
        phone_number: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar: str | None = None,
        sharing: dict | None = None,
    ) -> dict:
        """Update contact card fields. Only provided fields are changed."""
        body: dict = {}
        if first_name is not None:
            body["first_name"] = first_name
        if last_name is not None:
            body["last_name"] = last_name
        if avatar is not None:
            body["avatar"] = avatar
        if sharing is not None:
            body["sharing"] = sharing
        resp = self.session.put(
            f"{self._number_url(phone_number)}/contact-card",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    def share_contact_card(self, chat_id: str) -> dict:
        """Stage contact card to be shared with the next outgoing message."""
        resp = self.session.post(f"{self._chat_url(chat_id)}/contact-card")
        resp.raise_for_status()
        return resp.json()

    def start_typing(self, chat_id: str) -> dict:
        resp = self.session.post(f"{self._chat_url(chat_id)}/typing")
        resp.raise_for_status()
        return resp.json()

    def stop_typing(self, chat_id: str) -> dict:
        resp = self.session.delete(f"{self._chat_url(chat_id)}/typing")
        resp.raise_for_status()
        return resp.json()

    @contextmanager
    def typing(self, chat_id: str):
        """Context manager: shows typing indicator while processing."""
        self.start_typing(chat_id)
        try:
            yield
        finally:
            self.stop_typing(chat_id)


if __name__ == "__main__":
    client = BlooioClient()
    try:
        me = client.check_auth()
        print("Authenticated successfully:", me)
    except requests.HTTPError as e:
        print(f"Auth failed ({e.response.status_code}): {e.response.text}")
