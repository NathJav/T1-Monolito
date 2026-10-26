"""Dublês de teste: um Moodle em memória e uma fonte i-Educar configurável.

O FakeMoodle reproduz as regras do Moodle que importam para a integração:
idnumber/shortname/username únicos, e-mail único (allowaccountssameemail = 0),
erro `invalidrecord` ao atualizar registro inexistente e inscrição idempotente.
"""

from __future__ import annotations

import itertools

from app.domain import DadosIEducar
from app.errors import MoodleError, MoodleIndisponivel


class FakeMoodle:
    def __init__(self):
        self._ids = itertools.count(100)
        self.categorias: dict[int, dict] = {}
        self.cursos: dict[int, dict] = {}
        self.usuarios: dict[int, dict] = {}
        self.inscricoes: dict[tuple[int, int], dict] = {}
        self.chamadas: list[str] = []
        self.falhar_em: dict[str, Exception] = {}
        self.fora_do_ar = False

    # utilitários de teste -------------------------------------------------
    def _registrar(self, nome: str, chave: str | None = None) -> None:
        if self.fora_do_ar:
            raise MoodleIndisponivel("Moodle indisponível (simulado)")
        self.chamadas.append(nome)
        erro = self.falhar_em.get(f"{nome}:{chave}") or self.falhar_em.get(nome)
        if erro:
            raise erro

    def contar_escritas(self) -> int:
        return sum(1 for c in self.chamadas if c.startswith(("criar", "atualizar", "inscrever")))

    @staticmethod
    def _por(colecao: dict, campo: str, valor) -> dict | None:
        return next((r for r in colecao.values() if r.get(campo) == valor), None)

    # API usada pelo serviço ----------------------------------------------
    def info_site(self) -> dict:
        self._registrar("info_site")
        return {"sitename": "Fake", "release": "5.0", "username": "integracao"}

    def buscar_categoria(self, idnumber):
        self._registrar("buscar_categoria")
        return self._por(self.categorias, "idnumber", idnumber)

    def criar_categoria(self, dados):
        self._registrar("criar_categoria", dados.get("idnumber"))
        if self._por(self.categorias, "idnumber", dados["idnumber"]):
            raise MoodleError("idnumber já usado", codigo="categoryidnumbertaken")
        novo = next(self._ids)
        self.categorias[novo] = {"id": novo, **dados}
        return novo

    def atualizar_categoria(self, categoria_id, dados):
        self._registrar("atualizar_categoria", dados.get("idnumber"))
        if categoria_id not in self.categorias:
            raise MoodleError("Registro não encontrado", codigo="invalidrecord")
        self.categorias[categoria_id].update(dados)

    def buscar_curso(self, idnumber):
        self._registrar("buscar_curso")
        return self._por(self.cursos, "idnumber", idnumber)

    def criar_curso(self, dados):
        self._registrar("criar_curso", dados.get("idnumber"))
        if self._por(self.cursos, "shortname", dados["shortname"]):
            raise MoodleError("Nome breve já em uso", codigo="shortnametaken")
        novo = next(self._ids)
        self.cursos[novo] = {"id": novo, **dados}
        return novo

    def atualizar_curso(self, curso_id, dados):
        self._registrar("atualizar_curso", dados.get("idnumber"))
        if curso_id not in self.cursos:
            raise MoodleError("Registro não encontrado", codigo="invalidrecord")
        self.cursos[curso_id].update(dados)

    def buscar_usuarios(self, campo, valores):
        self._registrar("buscar_usuarios")
        valores = list(valores)
        return [u for u in self.usuarios.values() if u.get(campo) in valores]

    def buscar_usuario(self, campo, valor):
        encontrados = self.buscar_usuarios(campo, [valor])
        return encontrados[0] if encontrados else None

    def criar_usuario(self, dados):
        self._registrar("criar_usuario", dados.get("idnumber"))
        if self._por(self.usuarios, "username", dados["username"]):
            raise MoodleError("Username já existe", codigo="invalidparameter")
        if self._por(self.usuarios, "email", dados["email"]):
            raise MoodleError("Email address already exists", codigo="invalidparameter")
        novo = next(self._ids)
        self.usuarios[novo] = {"id": novo, **dados}
        return novo

    def atualizar_usuario(self, usuario_id, dados):
        self._registrar("atualizar_usuario", dados.get("idnumber"))
        if usuario_id not in self.usuarios:
            raise MoodleError("Usuário inválido", codigo="invaliduser")
        dono = self._por(self.usuarios, "email", dados.get("email"))
        if dono and dono["id"] != usuario_id:
            raise MoodleError("Email address already exists", codigo="invalidparameter")
        self.usuarios[usuario_id].update(dados)

    def inscrever(self, usuario_id, curso_id, papel_id, suspenso=False):
        self._registrar("inscrever", f"{usuario_id}:{curso_id}")
        if curso_id not in self.cursos or usuario_id not in self.usuarios:
            raise MoodleError("Invalid parameter value detected", codigo="invalidparameter")
        self.inscricoes[(usuario_id, curso_id)] = {"papel": papel_id, "suspenso": suspenso}

    def usuarios_inscritos(self, curso_id):
        return [self.usuarios[u] for (u, c) in self.inscricoes if c == curso_id]


class FakeFonte:
    def __init__(self, dados: DadosIEducar | None = None):
        self.dados = dados or DadosIEducar()
        self.erro: Exception | None = None

    def verificar(self) -> dict:
        return {"versao": "fake"}

    def carregar(self, ano: int) -> DadosIEducar:
        if self.erro:
            raise self.erro
        return self.dados
