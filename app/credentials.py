import json

import httpx
from dateutil.parser import parse as date_parse, ParserError
from google.auth.credentials import TokenState
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token
from google_auth_oauthlib.flow import Flow as OAuthFlow
from google.oauth2.credentials import Credentials as OAuthCredentials
from oauthlib.oauth2 import OAuth2Error

from app.pipeline import Result, Ok, Something, Option, as_async_result, as_result
from app.session import QuickStorage
from app.settings import config


class ServiceCredentialStorage:

    _CREDENTIALS_EXP = 3600 * 24 * 182

    def __init__(self, storage: QuickStorage | None = None):
        self._storage = storage or QuickStorage(prefix="CREDENTIALS:")

    async def save(self, email: str, creds: OAuthCredentials) -> Result[bool, Exception]:
        async with self._storage.connect() as client:
            match await self._storage.set(email, creds.to_json(), client=client):
                case Ok(status):
                    return (
                        await self._storage.set_expiry(email, self._CREDENTIALS_EXP, client=client)
                        if status
                        else Ok(False)
                    )

                case err: return err

    async def get(self, email: str) -> Result[Option[OAuthCredentials], Exception]:
        async with self._storage.connect() as client:
            match await self._storage.get(email, client=client):
                case Ok(Something(creds_raw)):
                    match self._convert(creds_raw):
                        case Ok(creds):
                            _ = await self._storage.set_expiry(email, self._CREDENTIALS_EXP, client=client)
                            return Ok(Something(creds))

                        case err: return err
                case err: return err

    async def delete(self, email: str) -> Result[int, Exception]:
        return await self._storage.delete(email)

    @staticmethod
    @as_result(ValueError, ParserError)
    def _convert(raw_credentials: str) -> OAuthCredentials:
        body = json.loads(raw_credentials)
        return OAuthCredentials(
            **{
                **body,
                "expiry": date_parse(body['expiry'], ignoretz=True)
            }
        )


class ServiceCredentialApi:

    CREDENTIALS_EXP = 3600 * 24 * 182
    REDIRECT_URI = f'http://{config().api_host}:{config().api_port}/{config().redirection_route}'

    OAUTH_SECRETS_FILE = config().oauth_secret_file
    SCOPES = (
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events.readonly',
        'https://www.googleapis.com/auth/gmail.send'
    )

    @staticmethod
    @as_async_result(OAuth2Error, ValueError)
    async def request() -> tuple[str, str]:
        flow = OAuthFlow.from_client_secrets_file(
            ServiceCredentialApi.OAUTH_SECRETS_FILE,
            scopes=ServiceCredentialApi.SCOPES,
            redirect_uri=ServiceCredentialApi.REDIRECT_URI,
        )

        return flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )

    @staticmethod
    @as_async_result(OAuth2Error, ValueError)
    async def obtain(state, request_uri: str) -> OAuthCredentials:
        flow = OAuthFlow.from_client_secrets_file(
            ServiceCredentialApi.OAUTH_SECRETS_FILE,
            scopes=ServiceCredentialApi.SCOPES,
            redirect_uri=ServiceCredentialApi.REDIRECT_URI,
            state=state
        )

        flow.fetch_token(authorization_response=request_uri)

        return flow.credentials

    @staticmethod
    @as_async_result(GoogleAuthError, ValueError)
    async def fetch_user_info(credentials: OAuthCredentials) -> dict:
        return verify_oauth2_token(
            id_token=credentials.id_token,
            audience=credentials.client_id,
            request=Request()
        )

    @staticmethod
    def credentials_valid(credentials: OAuthCredentials) -> bool:
        return credentials.token_state == TokenState.FRESH

    @staticmethod
    @as_async_result(OAuth2Error)
    async def refresh(credentials: OAuthCredentials) -> OAuthCredentials:
        credentials.refresh(Request())
        return credentials

    @staticmethod
    @as_async_result(httpx.HTTPError)
    async def revoke(credentials: OAuthCredentials) -> int:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": credentials.token},
                headers={'content-type': 'application/x-www-form-urlencoded'}
            )
            return response.status_code
