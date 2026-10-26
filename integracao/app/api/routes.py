"""Interface HTTP da aplicação (REST + painel web)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.errors import IntegracaoError, SincronizacaoEmAndamento
from app.sync import mapper

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "web" / "templates"))

TIPOS_VINCULO = {"escola", "turma", "aluno", "matricula"}


def _container(request: Request):
    return request.app.state.container


# ------------------------------------------------------------------ painel
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def painel(request: Request):
    container = _container(request)
    repo = container.repositorio
    return templates.TemplateResponse(
        request,
        "painel.html",
        {
            "settings": container.settings,
            "execucoes": repo.listar_execucoes(10),
            "vinculos": repo.contar_vinculos(),
            "token_ieducar": bool(container.settings.token_ieducar()),
            "token_moodle": bool(container.settings.token_moodle()),
            "agendador": request.app.state.agendador.ativo,
        },
    )


# ------------------------------------------------------------------- saúde
@router.get("/health", tags=["saúde"])
def health():
    """Liveness: a aplicação está no ar."""
    return {"status": "ok"}


@router.get("/saude", tags=["saúde"])
def saude(request: Request):
    """Verifica a comunicação com o i-Educar e com o Moodle."""
    container = _container(request)
    resultado: dict = {}
    try:
        resultado["ieducar"] = {"ok": True, **container.fonte().verificar()}
    except IntegracaoError as exc:
        resultado["ieducar"] = {"ok": False, "erro": str(exc)}
    try:
        info = container.moodle().info_site()
        resultado["moodle"] = {
            "ok": True,
            "site": info.get("sitename"),
            "versao": info.get("release"),
            "usuario": info.get("username"),
        }
    except IntegracaoError as exc:
        resultado["moodle"] = {"ok": False, "erro": str(exc)}
    resultado["ok"] = resultado["ieducar"]["ok"] and resultado["moodle"]["ok"]
    return resultado


# ---------------------------------------------------------- sincronização
@router.post("/sincronizacoes", tags=["sincronização"], status_code=201)
def sincronizar(
    request: Request,
    ano: int | None = Query(None, description="Ano letivo (padrão: configurado)"),
    completa: bool = Query(False, description="Ignora o cache de hashes e confere todos os registros no Moodle"),
):
    """Executa a sincronização i-Educar → Moodle e devolve o relatório."""
    container = _container(request)
    try:
        return container.servico().executar(
            ano or container.settings.ano_letivo, gatilho="manual", completa=completa
        )
    except SincronizacaoEmAndamento as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sincronizacoes", tags=["sincronização"])
def listar_sincronizacoes(request: Request, limite: int = Query(20, ge=1, le=200)):
    return _container(request).repositorio.listar_execucoes(limite)


@router.get("/sincronizacoes/{execucao_id}", tags=["sincronização"])
def obter_sincronizacao(request: Request, execucao_id: int):
    execucao = _container(request).repositorio.obter_execucao(execucao_id)
    if not execucao:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    return execucao


@router.get("/vinculos", tags=["sincronização"])
def listar_vinculos(request: Request, tipo: str | None = None, limite: int = Query(500, ge=1, le=5000)):
    if tipo and tipo not in TIPOS_VINCULO:
        raise HTTPException(status_code=400, detail=f"tipo deve ser um de {sorted(TIPOS_VINCULO)}")
    return [vars(v) for v in _container(request).repositorio.listar_vinculos(tipo, limite)]


# --------------------------------------------------- apoio à demonstração
@router.get("/ieducar/dados", tags=["demonstração"])
def dados_ieducar(request: Request, ano: int | None = None):
    """Dados de origem lidos do i-Educar, no formato canônico da integração."""
    container = _container(request)
    try:
        dados = container.fonte().carregar(ano or container.settings.ano_letivo)
    except IntegracaoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "totais": {
            "escolas": len(dados.escolas),
            "turmas": len(dados.turmas),
            "alunos": len(dados.alunos),
            "matriculas": len(dados.matriculas),
        },
        "escolas": [vars(e) for e in dados.escolas],
        "turmas": [vars(t) for t in dados.turmas],
        "alunos": [vars(a) for a in dados.alunos],
        "matriculas": [{**vars(m), "deve_ter_acesso": m.deve_ter_acesso} for m in dados.matriculas],
    }


@router.get("/moodle/turmas/{turma_id}", tags=["demonstração"])
def turma_no_moodle(request: Request, turma_id: int):
    """Curso e participantes no Moodle correspondentes a uma turma do i-Educar."""
    container = _container(request)
    moodle = container.moodle()
    try:
        curso = moodle.buscar_curso(mapper.idnumber("turma", turma_id))
        if not curso:
            raise HTTPException(status_code=404, detail="Turma ainda não sincronizada")
        inscritos = moodle.usuarios_inscritos(int(curso["id"]))
    except IntegracaoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "curso": {k: curso.get(k) for k in ("id", "fullname", "shortname", "idnumber", "categoryid", "visible")},
        "url": f"{container.settings.moodle_url_publica}/user/index.php?id={curso['id']}",
        "total_inscritos": len(inscritos),
        "inscritos": [
            {k: u.get(k) for k in ("id", "username", "fullname", "email", "idnumber")} for u in inscritos
        ],
    }
