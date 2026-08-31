"""
Token Manager integration for AdaDo Core.
Handles storing and retrieving customer tokens across all services.
"""
import httpx
import time
from fastapi import HTTPException


TM_BASE = 'http://127.0.0.1:8086'


class TokenManager:
    """Client for AdaDo Token Manager service."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client(base_url=TM_BASE)

    def _headers(self) -> dict:
        return {'X-Api-Key': self.api_key}

    def store_token(self, provider: str, customer_id: str, token: str,
                   token_type: str = 'access_token',
                   expires_at: float = None,
                   metadata: dict = None) -> dict:
        """Store a token for a customer."""
        account = f'customer:{customer_id}'
        r = self.client.post(
            '/token/store',
            json={
                'provider': provider,
                'account': account,
                'token_type': token_type,
                'token': token,
                'expires_at': expires_at,
                'metadata': metadata or {},
            },
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_token(self, provider: str, customer_id: str, token_type: str = 'access_token') -> str:
        """Retrieve a stored token for a customer."""
        account = f'customer:{customer_id}'
        r = self.client.get(
            f'/token/{provider}/{account}',
            params={'token_type': token_type},
            headers=self._headers(),
            timeout=15,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        return data.get('value')

    def list_customer_tokens(self, customer_id: str) -> list[dict]:
        """List all tokens for a customer."""
        account = f'customer:{customer_id}'
        r = self.client.get(
            f'/token/list/{account}',
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def delete_token(self, provider: str, customer_id: str, token_type: str = 'access_token') -> bool:
        """Delete a stored token."""
        account = f'customer:{customer_id}'
        r = self.client.delete(
            f'/token/{provider}/{account}',
            params={'token_type': token_type},
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get('deleted', False)


# Provider-specific onboarding handlers

class FacebookOnboarding:
    """Facebook OAuth token onboarding for AdaDo customers."""

    @staticmethod
    async def onboard(tm: TokenManager, customer_id: str, short_lived_token: str) -> dict:
        """
        Onboard Facebook from short-lived OAuth token.
        Exchanges for long-lived token, stores user token and all page tokens.
        """
        import sys
        sys.path.insert(0, '/home/ada')
        from adado_token_manager import facebook

        try:
            result = facebook.onboard(short_lived_token)

            # Store user token
            tm.store_token(
                provider='facebook',
                customer_id=customer_id,
                token=result.get('access_token'),
                token_type='user_token',
                expires_at=result.get('user_token_expires_at'),
            )

            # Store page tokens
            for page in result.get('pages_stored', []):
                tm.store_token(
                    provider='facebook',
                    customer_id=f'{customer_id}:page:{page["id"]}',
                    token=page.get('access_token'),
                    token_type='page_token',
                    metadata={'page_id': page['id'], 'page_name': page['name']},
                )

            return {
                'status': 'ok',
                'user_token_expires_at': result.get('user_token_expires_at'),
                'pages_onboarded': len(result.get('pages_stored', [])),
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f'Facebook onboarding failed: {e}')


class StripeOnboarding:
    """Stripe API key onboarding for AdaDo customers."""

    @staticmethod
    async def onboard(tm: TokenManager, customer_id: str, api_key: str) -> dict:
        """Store Stripe API key. Keys don't expire but rotation is recommended yearly."""
        tm.store_token(
            provider='stripe',
            customer_id=customer_id,
            token=api_key,
            token_type='secret_key',
            metadata={'rotation_recommended': 'yearly'},
        )
        return {'status': 'ok', 'message': 'Stripe API key stored securely'}


class FastmailOnboarding:
    """Fastmail JMAP token onboarding for AdaDo customers."""

    @staticmethod
    async def onboard(tm: TokenManager, customer_id: str, api_token: str, account_id: str) -> dict:
        """Store Fastmail JMAP credentials."""
        tm.store_token(
            provider='fastmail',
            customer_id=customer_id,
            token=api_token,
            token_type='jmap_token',
            metadata={'account_id': account_id},
        )
        return {'status': 'ok', 'message': 'Fastmail JMAP token stored securely'}


class ElevenLabsOnboarding:
    """ElevenLabs API key onboarding for AdaDo customers."""

    @staticmethod
    async def onboard(tm: TokenManager, customer_id: str, api_key: str) -> dict:
        """Store ElevenLabs API key. Keys don't expire."""
        tm.store_token(
            provider='elevenlabs',
            customer_id=customer_id,
            token=api_key,
            token_type='api_key',
        )
        return {'status': 'ok', 'message': 'ElevenLabs API key stored securely'}
