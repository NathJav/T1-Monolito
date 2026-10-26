"""Configuração da aplicação, lida de variáveis de ambiente.

Os tokens podem vir diretamente de variáveis (IEDUCAR_TOKEN / MOODLE_TOKEN) ou de
arquivos (IEDUCAR_TOKEN_FILE / MOODLE_TOKEN_FILE). Os arquivos são gerados
automaticamente pelos containers do i-Educar e do Moodle no primeiro boot.
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _ler_token(valor: str | None, arquivo: str | None) -> str | None:
    if valor:
        return valor.strip()
    if arquivo and Path(arquivo).is_file():
        conteudo = Path(arquivo).read_text(encoding="utf-8").strip()
        return conteudo or None
    return None


class Settings(BaseModel):
    ieducar_url: str = "http://ieducar-nginx"
    ieducar_token: str | None = None
    ieducar_token_file: str | None = "/shared/ieducar_token"
    ieducar_page_size: int = 100

    ieducar_url_publica: str = "http://localhost:8000"

    moodle_url: str = "http://moodle:8080"
    moodle_url_publica: str = "http://localhost:8080"
    moodle_token: str | None = None
    moodle_token_file: str | None = "/shared/moodle_token"
    moodle_student_role_id: int = 5
    moodle_criar_senha: bool = True

    ano_letivo: int = Field(default_factory=lambda: date.today().year)
    intervalo_sync_minutos: int = 5
    reconciliacao_a_cada: int = 12
    database_path: str = "/data/integracao.db"

    http_timeout: float = 30.0
    http_tentativas: int = 3
    dominio_email_placeholder: str = "sem-email.invalid"

    @classmethod
    def from_env(cls) -> "Settings":
        dados: dict = {}
        mapa = {
            "IEDUCAR_URL": "ieducar_url",
            "IEDUCAR_TOKEN": "ieducar_token",
            "IEDUCAR_TOKEN_FILE": "ieducar_token_file",
            "IEDUCAR_PAGE_SIZE": "ieducar_page_size",
            "IEDUCAR_URL_PUBLICA": "ieducar_url_publica",
            "MOODLE_URL": "moodle_url",
            "MOODLE_URL_PUBLICA": "moodle_url_publica",
            "MOODLE_TOKEN": "moodle_token",
            "MOODLE_TOKEN_FILE": "moodle_token_file",
            "MOODLE_STUDENT_ROLE_ID": "moodle_student_role_id",
            "MOODLE_CRIAR_SENHA": "moodle_criar_senha",
            "SYNC_ANO_LETIVO": "ano_letivo",
            "SYNC_INTERVALO_MINUTOS": "intervalo_sync_minutos",
            "SYNC_RECONCILIACAO_A_CADA": "reconciliacao_a_cada",
            "DATABASE_PATH": "database_path",
            "HTTP_TIMEOUT": "http_timeout",
            "HTTP_TENTATIVAS": "http_tentativas",
            "EMAIL_PLACEHOLDER_DOMINIO": "dominio_email_placeholder",
        }
        for env_name, campo in mapa.items():
            valor = _env(env_name)
            if valor is not None:
                dados[campo] = valor
        return cls(**dados)

    def token_ieducar(self) -> str | None:
        return _ler_token(self.ieducar_token, self.ieducar_token_file)

    def token_moodle(self) -> str | None:
        return _ler_token(self.moodle_token, self.moodle_token_file)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
