"""Ponto de entrada da aplicação monolítica de integração i-Educar → Moodle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, get_settings
from app.container import Container
from app.scheduler import Agendador

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or Container(settings)

    execucoes_agendadas = {"total": 0}

    def tarefa_agendada():
        # A cada N execuções agendadas, faz uma reconciliação completa (ignora o cache).
        execucoes_agendadas["total"] += 1
        n = settings.reconciliacao_a_cada
        completa = n > 0 and execucoes_agendadas["total"] % n == 0
        return container.servico().executar(settings.ano_letivo, gatilho="agendado", completa=completa)

    agendador = Agendador(settings.intervalo_sync_minutos, tarefa_agendada)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        agendador.iniciar()
        yield
        agendador.parar()
        container.fechar()

    app = FastAPI(
        title="Integração i-Educar → Moodle",
        description=(
            "Aplicação monolítica que sincroniza escolas, turmas, alunos e matrículas "
            "do i-Educar com categorias, cursos, usuários e inscrições do Moodle."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.agendador = agendador
    app.include_router(router)
    return app
