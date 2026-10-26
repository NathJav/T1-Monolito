"""Cliente dos Web Services REST do Moodle.

Encapsula o protocolo do Moodle (POST para /webservice/rest/server.php com
parâmetros no formato de arrays do PHP) e transforma respostas de erro em
`MoodleError`. Falhas de rede e respostas 5xx são repetidas com espera exponencial.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

import httpx

from app.errors import ConfiguracaoError, MoodleError, MoodleIndisponivel

logger = logging.getLogger(__name__)

ENDPOINT = "/webservice/rest/server.php"


def achatar_parametros(valor: Any, prefixo: str = "") -> list[tuple[str, str]]:
    """Converte dicts/listas aninhados no formato `users[0][username]=x` do PHP."""
    pares: list[tuple[str, str]] = []
    if isinstance(valor, dict):
        for chave, item in valor.items():
            nome = f"{prefixo}[{chave}]" if prefixo else str(chave)
            pares.extend(achatar_parametros(item, nome))
    elif isinstance(valor, (list, tuple)):
        for indice, item in enumerate(valor):
            pares.extend(achatar_parametros(item, f"{prefixo}[{indice}]"))
    elif valor is None:
        return pares
    elif isinstance(valor, bool):
        pares.append((prefixo, "1" if valor else "0"))
    else:
        pares.append((prefixo, str(valor)))
    return pares


class MoodleClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        http: httpx.Client | None = None,
        tentativas: int = 3,
        timeout: float = 30.0,
        espera_base: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or httpx.Client(timeout=timeout)
        self.tentativas = max(1, tentativas)
        self.espera_base = espera_base

    # ------------------------------------------------------------------ núcleo
    def chamar(self, funcao: str, **parametros: Any) -> Any:
        if not self.token:
            raise ConfiguracaoError(
                "Token do Moodle não configurado (MOODLE_TOKEN ou MOODLE_TOKEN_FILE)."
            )
        # As chaves achatadas são únicas (users[0][username], users[1][username], ...).
        dados = {
            "wstoken": self.token,
            "wsfunction": funcao,
            "moodlewsrestformat": "json",
            **dict(achatar_parametros(parametros)),
        }
        url = f"{self.base_url}{ENDPOINT}"
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                resposta = self.http.post(url, data=dados)
                if resposta.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resposta.status_code}", request=resposta.request, response=resposta
                    )
                resposta.raise_for_status()
                corpo = resposta.json() if resposta.content else None
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                ultimo_erro = exc
                transitorio = isinstance(exc, httpx.TransportError) or (
                    exc.response is not None and exc.response.status_code >= 500
                )
                if not transitorio:
                    raise MoodleError(f"Moodle recusou {funcao}: {exc}") from exc
                if tentativa == self.tentativas:
                    raise MoodleIndisponivel(f"Moodle indisponível em {funcao}: {exc}") from exc
                espera = self.espera_base * 2 ** (tentativa - 1)
                logger.warning("Moodle %s falhou (%s); nova tentativa em %.1fs", funcao, exc, espera)
                time.sleep(espera)
            except ValueError as exc:
                raise MoodleError(f"Resposta inválida do Moodle em {funcao}") from exc
        else:  # pragma: no cover - laço sempre sai por break ou raise
            raise MoodleError(str(ultimo_erro))

        if isinstance(corpo, dict) and "exception" in corpo:
            raise MoodleError(
                corpo.get("message") or corpo["exception"],
                codigo=corpo.get("errorcode"),
                debug=corpo.get("debuginfo"),
            )
        return corpo

    # ------------------------------------------------------------------ site
    def info_site(self) -> dict:
        return self.chamar("core_webservice_get_site_info")

    # ------------------------------------------------------------- categorias
    def buscar_categoria(self, idnumber: str) -> dict | None:
        resultado = self.chamar(
            "core_course_get_categories",
            criteria=[{"key": "idnumber", "value": idnumber}],
            addsubcategories=0,
        )
        return resultado[0] if resultado else None

    def criar_categoria(self, dados: dict) -> int:
        resultado = self.chamar("core_course_create_categories", categories=[dados])
        return int(resultado[0]["id"])

    def atualizar_categoria(self, categoria_id: int, dados: dict) -> None:
        self.chamar("core_course_update_categories", categories=[{"id": categoria_id, **dados}])

    # ----------------------------------------------------------------- cursos
    def buscar_curso(self, idnumber: str) -> dict | None:
        resultado = self.chamar("core_course_get_courses_by_field", field="idnumber", value=idnumber)
        cursos = (resultado or {}).get("courses", [])
        return cursos[0] if cursos else None

    def buscar_curso_por_id(self, curso_id: int) -> dict | None:
        resultado = self.chamar("core_course_get_courses_by_field", field="id", value=curso_id)
        cursos = (resultado or {}).get("courses", [])
        return cursos[0] if cursos else None

    def criar_curso(self, dados: dict) -> int:
        resultado = self.chamar("core_course_create_courses", courses=[dados])
        return int(resultado[0]["id"])

    def atualizar_curso(self, curso_id: int, dados: dict) -> None:
        resultado = self.chamar("core_course_update_courses", courses=[{"id": curso_id, **dados}])
        self._verificar_avisos(resultado, "core_course_update_courses")

    # --------------------------------------------------------------- usuários
    def buscar_usuarios(self, campo: str, valores: Iterable[str]) -> list[dict]:
        return self.chamar("core_user_get_users_by_field", field=campo, values=list(valores)) or []

    def buscar_usuario(self, campo: str, valor: str) -> dict | None:
        usuarios = self.buscar_usuarios(campo, [valor])
        return usuarios[0] if usuarios else None

    def criar_usuario(self, dados: dict) -> int:
        resultado = self.chamar("core_user_create_users", users=[dados])
        return int(resultado[0]["id"])

    def atualizar_usuario(self, usuario_id: int, dados: dict) -> None:
        resultado = self.chamar("core_user_update_users", users=[{"id": usuario_id, **dados}])
        self._verificar_avisos(resultado, "core_user_update_users")

    # ------------------------------------------------------------ inscrições
    def inscrever(self, usuario_id: int, curso_id: int, papel_id: int, suspenso: bool = False) -> None:
        self.chamar(
            "enrol_manual_enrol_users",
            enrolments=[
                {
                    "roleid": papel_id,
                    "userid": usuario_id,
                    "courseid": curso_id,
                    "suspend": 1 if suspenso else 0,
                }
            ],
        )

    def usuarios_inscritos(self, curso_id: int) -> list[dict]:
        return self.chamar("core_enrol_get_enrolled_users", courseid=curso_id) or []

    # --------------------------------------------------------------- apoio
    @staticmethod
    def _verificar_avisos(resultado: Any, funcao: str) -> None:
        avisos = (resultado or {}).get("warnings") if isinstance(resultado, dict) else None
        if avisos:
            aviso = avisos[0]
            raise MoodleError(
                aviso.get("message", f"Aviso em {funcao}"),
                codigo=aviso.get("warningcode"),
            )
