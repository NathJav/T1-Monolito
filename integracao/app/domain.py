"""Modelo canônico da integração.

Os dados do i-Educar são convertidos para estas estruturas antes de qualquer regra
de sincronização. Assim, as regras não dependem do formato de origem (API ou banco)
nem do formato de destino (payloads do Moodle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class SituacaoMatricula(IntEnum):
    """Códigos da coluna pmieducar.matricula.aprovado no i-Educar."""

    APROVADO = 1
    REPROVADO = 2
    CURSANDO = 3
    TRANSFERIDO = 4
    RECLASSIFICADO = 5
    ABANDONO = 6
    EM_EXAME = 7
    APROVADO_APOS_EXAME = 8
    APROVADO_SEM_EXAME = 10
    PRE_MATRICULA = 11
    APROVADO_COM_DEPENDENCIA = 12
    APROVADO_PELO_CONSELHO = 13
    REPROVADO_POR_FALTAS = 14
    FALECIDO = 15

    @classmethod
    def de_codigo(cls, codigo: int | None) -> "SituacaoMatricula | None":
        try:
            return cls(int(codigo)) if codigo is not None else None
        except ValueError:
            return None


# Situações em que o aluno ainda deve ter acesso ao curso no Moodle.
SITUACOES_ACESSO_ATIVO = {
    SituacaoMatricula.CURSANDO,
    SituacaoMatricula.EM_EXAME,
}


@dataclass(frozen=True)
class Escola:
    id: int
    nome: str
    ativo: bool = True


@dataclass(frozen=True)
class Turma:
    id: int
    nome: str
    ano: int
    escola_id: int
    ativo: bool = True
    serie: str | None = None
    curso: str | None = None


@dataclass(frozen=True)
class Aluno:
    id: int
    nome: str
    email: str | None = None


@dataclass(frozen=True)
class Matricula:
    id: int
    aluno_id: int
    turma_id: int
    ano: int
    situacao: int | None
    ativa: bool = True

    @property
    def deve_ter_acesso(self) -> bool:
        situacao = SituacaoMatricula.de_codigo(self.situacao)
        return self.ativa and situacao in SITUACOES_ACESSO_ATIVO


@dataclass
class DadosIEducar:
    """Retrato dos dados de origem considerados em uma execução."""

    escolas: list[Escola] = field(default_factory=list)
    turmas: list[Turma] = field(default_factory=list)
    alunos: list[Aluno] = field(default_factory=list)
    matriculas: list[Matricula] = field(default_factory=list)
