from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.domain import Aluno, DadosIEducar, Escola, Matricula, SituacaoMatricula, Turma  # noqa: E402
from app.storage import Repositorio  # noqa: E402
from app.sync.service import SyncService  # noqa: E402
from fakes import FakeFonte, FakeMoodle  # noqa: E402

ANO = 2026


def dados_exemplo() -> DadosIEducar:
    return DadosIEducar(
        escolas=[Escola(1, "ESCOLA MUNICIPAL CENTRO"), Escola(2, "Escola do Bairro")],
        turmas=[
            Turma(10, "5º Ano A", ANO, 1, serie="5º ano", curso="Ensino Fundamental"),
            Turma(11, "5º Ano B", ANO, 1, serie="5º ano"),
            Turma(20, "1º Ano A", ANO, 2, serie="1º ano"),
        ],
        alunos=[
            Aluno(100, "MARIA DA SILVA", "maria@exemplo.com"),
            Aluno(101, "João Pereira", None),
            Aluno(102, "Ana", "email-invalido"),
        ],
        matriculas=[
            Matricula(1000, 100, 10, ANO, SituacaoMatricula.CURSANDO),
            Matricula(1001, 101, 10, ANO, SituacaoMatricula.CURSANDO),
            Matricula(1002, 102, 20, ANO, SituacaoMatricula.CURSANDO),
        ],
    )


@pytest.fixture
def fonte():
    return FakeFonte(dados_exemplo())


@pytest.fixture
def moodle():
    return FakeMoodle()


@pytest.fixture
def repo():
    return Repositorio(":memory:")


@pytest.fixture
def servico(fonte, moodle, repo):
    return SyncService(fonte, moodle, repo, papel_estudante_id=5)
