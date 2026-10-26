"""Montagem das dependências (injeção manual).

Os tokens são relidos a cada uso: no primeiro `docker compose up` eles só passam a
existir depois que o i-Educar e o Moodle terminam a instalação, e a aplicação não
precisa ser reiniciada para enxergá-los.
"""

from __future__ import annotations

import threading

import httpx

from app.config import Settings
from app.ieducar.api_source import IEducarApiSource
from app.moodle.client import MoodleClient
from app.storage import Repositorio
from app.sync.service import SyncService


class Container:
    def __init__(self, settings: Settings, repositorio: Repositorio | None = None):
        self.settings = settings
        self.repositorio = repositorio or Repositorio(settings.database_path)
        self._http = httpx.Client(timeout=settings.http_timeout, follow_redirects=True)
        self._trava = threading.Lock()
        self._servico: SyncService | None = None

    def fonte(self) -> IEducarApiSource:
        return IEducarApiSource(
            self.settings.ieducar_url,
            self.settings.token_ieducar(),
            http=self._http,
            tamanho_pagina=self.settings.ieducar_page_size,
            tentativas=self.settings.http_tentativas,
        )

    def moodle(self) -> MoodleClient:
        return MoodleClient(
            self.settings.moodle_url,
            self.settings.token_moodle(),
            http=self._http,
            tentativas=self.settings.http_tentativas,
        )

    def servico(self) -> SyncService:
        """Serviço único (a trava de exclusão mútua vive nele), com clientes atualizados."""
        with self._trava:
            if self._servico is None:
                self._servico = SyncService(
                    self.fonte(),
                    self.moodle(),
                    self.repositorio,
                    papel_estudante_id=self.settings.moodle_student_role_id,
                    criar_senha=self.settings.moodle_criar_senha,
                    dominio_email_placeholder=self.settings.dominio_email_placeholder,
                )
            elif not self._servico.em_andamento:
                self._servico.fonte = self.fonte()
                self._servico.moodle = self.moodle()
            return self._servico

    def fechar(self) -> None:
        self._http.close()
