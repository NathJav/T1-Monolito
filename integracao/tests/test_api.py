"""Endpoints HTTP da aplicação, com dublês no lugar dos sistemas externos."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import Container
from app.main import create_app
from app.storage import Repositorio
from app.sync.service import SyncService
from conftest import dados_exemplo
from fakes import FakeFonte, FakeMoodle


class ContainerFalso(Container):
    def __init__(self):
        self.settings = Settings(ano_letivo=2026, intervalo_sync_minutos=0, ieducar_token="x", moodle_token="y")
        self.repositorio = Repositorio(":memory:")
        self._fonte = FakeFonte(dados_exemplo())
        self._moodle = FakeMoodle()
        self._servico = SyncService(self._fonte, self._moodle, self.repositorio)

    def fonte(self):
        return self._fonte

    def moodle(self):
        return self._moodle

    def servico(self):
        return self._servico

    def fechar(self):
        pass


@pytest.fixture
def cliente():
    container = ContainerFalso()
    app = create_app(container.settings, container)
    with TestClient(app) as c:
        c.container = container
        yield c


def test_health(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}


def test_saude_verifica_os_dois_sistemas(cliente):
    corpo = cliente.get("/saude").json()
    assert corpo["ok"] is True
    assert corpo["moodle"]["usuario"] == "integracao"


def test_sincronizar_e_consultar_historico(cliente):
    r = cliente.post("/sincronizacoes")
    assert r.status_code == 201
    execucao_id = r.json()["execucao_id"]

    detalhe = cliente.get(f"/sincronizacoes/{execucao_id}").json()
    assert detalhe["status"] == "sucesso"
    assert cliente.get("/sincronizacoes").json()[0]["id"] == execucao_id
    assert len(cliente.get("/vinculos", params={"tipo": "turma"}).json()) == 3


def test_execucao_concorrente_responde_409(cliente):
    servico = cliente.container.servico()
    servico._trava.acquire()
    try:
        assert cliente.post("/sincronizacoes").status_code == 409
    finally:
        servico._trava.release()


def test_tipo_de_vinculo_invalido(cliente):
    assert cliente.get("/vinculos", params={"tipo": "x"}).status_code == 400


def test_execucao_inexistente(cliente):
    assert cliente.get("/sincronizacoes/999").status_code == 404


def test_painel_renderiza(cliente):
    cliente.post("/sincronizacoes")
    html = cliente.get("/").text
    assert "Integração i-Educar → Moodle" in html
    assert "sucesso" in html


def test_dados_de_origem_e_turma_no_moodle(cliente):
    origem = cliente.get("/ieducar/dados").json()
    assert origem["totais"] == {"escolas": 2, "turmas": 3, "alunos": 3, "matriculas": 3}

    assert cliente.get("/moodle/turmas/10").status_code == 404
    cliente.post("/sincronizacoes")
    turma = cliente.get("/moodle/turmas/10").json()
    assert turma["curso"]["idnumber"] == "ieducar-turma-10"
    assert turma["total_inscritos"] == 2
