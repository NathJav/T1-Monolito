"""Mapeamento do modelo canônico (i-Educar) para os payloads do Moodle.

Funções puras: recebem objetos de domínio e devolvem dicionários prontos para a
API do Moodle. O campo `idnumber` carrega a chave de origem e é a base da
prevenção de duplicidades: antes de criar, a integração procura o registro por
esse identificador.

    Escola    -> Categoria de curso   idnumber = ieducar-escola-<cod_escola>
    Turma     -> Curso                idnumber = ieducar-turma-<cod_turma>
    Aluno     -> Usuário              idnumber = ieducar-aluno-<cod_aluno>
    Matrícula -> Inscrição manual (papel estudante; suspensa quando a matrícula
                 deixa de estar em andamento)
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from app.domain import Aluno, Escola, Turma

PREFIXO = "ieducar"
LIMITE_NOME = 100
LIMITE_CURSO = 254

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def idnumber(tipo: str, id_origem: int | str) -> str:
    return f"{PREFIXO}-{tipo}-{id_origem}"


def hash_payload(payload: dict) -> str:
    serializado = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _limpar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", (texto or "")).strip()


def _cortar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def email_valido(email: str | None) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


def email_placeholder(aluno_id: int, dominio: str) -> str:
    return f"aluno{aluno_id}@{dominio}"


def separar_nome(nome_completo: str) -> tuple[str, str]:
    partes = _limpar(nome_completo).split(" ")
    if not partes or not partes[0]:
        return "Sem nome", "(i-Educar)"
    if len(partes) == 1:
        # O Moodle exige sobrenome; o i-Educar permite nome único.
        return _cortar(partes[0], LIMITE_NOME), "-"
    return _cortar(partes[0], LIMITE_NOME), _cortar(" ".join(partes[1:]), LIMITE_NOME)


def username_aluno(aluno_id: int) -> str:
    return f"aluno{aluno_id}"


def _titulo(texto: str) -> str:
    """Normaliza nomes em CAIXA ALTA, comuns no i-Educar, para exibição."""
    texto = _limpar(texto)
    if texto and texto == texto.upper():
        minusculas = {"da", "de", "do", "das", "dos", "e"}
        palavras = [p.capitalize() if p.lower() not in minusculas else p.lower() for p in texto.split(" ")]
        palavras[0] = palavras[0].capitalize()
        return " ".join(palavras)
    return texto


def sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


# ---------------------------------------------------------------- payloads
def categoria(escola: Escola) -> dict:
    return {
        "name": _cortar(_titulo(escola.nome) or f"Escola {escola.id}", 255),
        "idnumber": idnumber("escola", escola.id),
        "description": f"Escola sincronizada do i-Educar (código {escola.id}).",
    }


def curso(turma: Turma, categoria_id: int, nome_escola: str | None = None) -> dict:
    nome = _titulo(turma.nome) or f"Turma {turma.id}"
    serie = _titulo(turma.serie or "")
    if serie and serie.lower() in nome.lower():
        serie = ""  # a série já faz parte do nome da turma
    detalhes = " · ".join(p for p in (serie, str(turma.ano)) if p)
    fullname = f"{nome} — {detalhes}" if detalhes else nome
    resumo = [f"Turma {turma.id} sincronizada do i-Educar."]
    if nome_escola:
        resumo.append(f"Escola: {_titulo(nome_escola)}.")
    if turma.curso:
        resumo.append(f"Curso: {_titulo(turma.curso)}.")
    return {
        "fullname": _cortar(fullname, LIMITE_CURSO),
        "shortname": _cortar(f"{nome} {turma.ano} #{turma.id}", 255),
        "categoryid": categoria_id,
        "idnumber": idnumber("turma", turma.id),
        "visible": 1 if turma.ativo else 0,
        "summary": " ".join(resumo),
        "summaryformat": 1,
    }


def usuario(aluno: Aluno, email: str) -> dict:
    """Campos atualizáveis do usuário (sem senha nem username)."""
    nome, sobrenome = separar_nome(_titulo(aluno.nome))
    return {
        "firstname": nome,
        "lastname": sobrenome,
        "email": email.lower(),
        "idnumber": idnumber("aluno", aluno.id),
    }


def novo_usuario(aluno: Aluno, email: str, criar_senha: bool, senha: str | None = None) -> dict:
    dados = {
        "username": username_aluno(aluno.id),
        "auth": "manual",
        **usuario(aluno, email),
    }
    if criar_senha:
        dados["createpassword"] = 1
    else:
        dados["password"] = senha
    return dados
