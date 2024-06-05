from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):

    api_host: str
    api_port: int
    redirection_route: str = "oauth2callback"
    event_listener_route: str = "listen_calendar_events"
    oauth_secret_file: str
    geo_api_key: str
    default_email_sender: str

    mongo_host: str
    mongo_port: int
    mongo_user: str = Field(alias='mongo_db_user')
    mongo_pass: str = Field(alias='mongo_db_password')
    mongo_db: str = Field(alias='mongo_database')

    redis_host: str
    redis_port: int
    redis_user: str = Field(alias='redis_username')
    redis_pass: str = Field(alias='redis_password')

    redis_pool_size: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def config():
    return Config()
