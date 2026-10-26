"""Exceções da integração, separadas por origem do problema."""

from __future__ import annotations


class IntegracaoError(Exception):
    """Erro base da aplicação."""


class ConfiguracaoError(IntegracaoError):
    """Falta configuração obrigatória (ex.: token ainda não gerado)."""


class IEducarError(IntegracaoError):
    """Falha ao consultar o i-Educar."""


class MoodleError(IntegracaoError):
    """Erro devolvido pela API de web services do Moodle."""

    def __init__(self, mensagem: str, codigo: str | None = None, debug: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.debug = debug

    @property
    def registro_inexistente(self) -> bool:
        return self.codigo in {"invalidrecord", "invalidrecordunknown", "invaliduser"}

    def __str__(self) -> str:  # pragma: no cover - apenas formatação
        base = super().__str__()
        extra = f" [{self.codigo}]" if self.codigo else ""
        detalhe = f" ({self.debug})" if self.debug else ""
        return f"{base}{extra}{detalhe}"


class MoodleIndisponivel(MoodleError):
    """Falha de rede/servidor ao falar com o Moodle: não adianta seguir com os demais registros."""


class SincronizacaoEmAndamento(IntegracaoError):
    """Uma execução já está rodando; evita execuções concorrentes."""


class DependenciaNaoSincronizada(IntegracaoError):
    """O registro depende de outro que ainda não existe no Moodle."""
