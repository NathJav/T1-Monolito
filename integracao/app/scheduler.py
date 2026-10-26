"""Agendador simples: dispara a sincronização a cada N minutos em uma thread."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from app.errors import SincronizacaoEmAndamento

logger = logging.getLogger(__name__)


class Agendador:
    def __init__(self, intervalo_minutos: int, tarefa: Callable[[], object]):
        self.intervalo = max(0, intervalo_minutos) * 60
        self.tarefa = tarefa
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def ativo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        if self.intervalo <= 0 or self.ativo:
            return
        self._thread = threading.Thread(target=self._laco, name="agendador-sync", daemon=True)
        self._thread.start()
        logger.info("Sincronização automática a cada %d min", self.intervalo // 60)

    def parar(self) -> None:
        self._parar.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _laco(self) -> None:
        while not self._parar.wait(self.intervalo):
            try:
                self.tarefa()
            except SincronizacaoEmAndamento:
                logger.info("Execução agendada ignorada: já existe uma em andamento")
            except Exception:  # noqa: BLE001 - o agendador nunca pode morrer
                logger.exception("Falha na sincronização agendada")
