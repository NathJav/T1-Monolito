"""Interface de leitura do i-Educar.

O serviço de sincronização depende apenas deste contrato. A implementação padrão
usa a API REST do i-Educar (`api_source.py`); outra fonte (por exemplo, leitura
direta do banco) pode ser adicionada sem alterar as regras de sincronização.
"""

from __future__ import annotations

from typing import Protocol

from app.domain import DadosIEducar


class FonteIEducar(Protocol):
    def verificar(self) -> dict:
        """Confere se o i-Educar responde e devolve informações básicas."""

    def carregar(self, ano: int) -> DadosIEducar:
        """Lê escolas, turmas, alunos e matrículas do ano letivo informado."""
