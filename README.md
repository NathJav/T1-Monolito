# T1 — Integração entre i-Educar e Moodle

Aplicação **monolítica** que sincroniza dados do [i-Educar](https://github.com/portabilis/i-educar) com o [Moodle](https://hub.docker.com/r/bitnamilegacy/moodle), evitando o recadastro de turmas e alunos no ambiente virtual.

| i-Educar | → | Moodle |
|---|---|---|
| Escola | → | Categoria de cursos |
| Turma | → | Curso |
| Aluno | → | Usuário |
| Matrícula / enturmação | → | Inscrição (papel estudante; suspensa em transferência, abandono ou remanejamento) |

A leitura usa a **API REST existente do i-Educar** e a escrita usa os **Web Services REST do Moodle**. Nenhum dos dois sistemas é alterado. A sincronização roda **a cada 5 minutos** e também pode ser disparada manualmente.

Disciplina de Projeto e Arquitetura de Software — Nathalie Jordão e Yasmin Weber.

---

## Sumário

1. [Requisitos](#1-requisitos)
2. [Como executar](#2-como-executar)
3. [Acessos](#3-acessos)
4. [Usando a integração](#4-usando-a-integração)
5. [Roteiro de demonstração](#5-roteiro-de-demonstração)
6. [Arquitetura](#6-arquitetura)
7. [Configuração](#7-configuração)
8. [Testes](#8-testes)
9. [Relatório](#9-relatório)
10. [Solução de problemas](#10-solução-de-problemas)

---

## 1. Requisitos

- Docker e Docker Compose v2 (Docker Desktop no Windows/macOS);
- cerca de **6 GB de RAM** livres para o Docker e **10 GB** de disco;
- acesso à internet no primeiro build (baixa imagens, o código do i-Educar e as dependências do Composer/pip);
- portas **8000**, **8080** e **8090** livres (podem ser trocadas no `.env`).

## 2. Como executar

```bash
git clone https://github.com/NathJav/T1-Monolito.git
cd T1-Monolito
cp .env.example .env          # opcional: só se quiser mudar portas ou senhas
docker compose up -d --build
```

**O primeiro boot é demorado** (de 10 a 20 minutos, conforme a máquina e a internet):

1. o build do i-Educar baixa o código oficial e roda o `composer install`;
2. o i-Educar roda as migrations, carrega os dados padrão e os **dados de demonstração oficiais** (`DemoSeeder`: 2 escolas, 21 turmas, 210 alunos) e gera o token da API;
3. o Moodle se instala e, em seguida, um script habilita os Web Services REST, cria o usuário de serviço e gera o token;
4. a aplicação de integração sobe quando o Moodle fica pronto.

Acompanhe com:

```bash
docker compose ps             # aguarde ieducar e moodle ficarem "healthy"
docker compose logs -f ieducar moodle integracao
```

Quando o log do Moodle mostrar `[integracao] Web services do Moodle configurados.`, está tudo pronto.

Para parar: `docker compose down`. Para apagar tudo e recomeçar do zero: `docker compose down -v`.

## 3. Acessos

| Sistema | Endereço | Usuário | Senha |
|---|---|---|---|
| i-Educar | http://localhost:8000 | `admin` | `123456789` |
| Moodle | http://localhost:8080 | `admin` | `Moodle@2026` |
| Integração (painel) | http://localhost:8090 | — | — |
| Integração (API/Swagger) | http://localhost:8090/docs | — | — |

> As senhas são de ambiente de desenvolvimento. Não use este ambiente em produção.

## 4. Usando a integração

**Painel web** (http://localhost:8090): botões *Sincronizar agora* e *Verificar conexões*, totais de registros vinculados e histórico das execuções, com links para o i-Educar e o Moodle.

**API REST**:

| Método e rota | Função |
|---|---|
| `GET /saude` | Testa a comunicação com o i-Educar e o Moodle |
| `POST /sincronizacoes` | Executa a sincronização e devolve o relatório |
| `POST /sincronizacoes?completa=true` | Reconciliação completa (ignora o cache e recria o que foi apagado no Moodle) |
| `GET /sincronizacoes` / `GET /sincronizacoes/{id}` | Histórico e detalhes (com erros por registro) |
| `GET /vinculos?tipo=turma` | Correspondências i-Educar ↔ Moodle (`escola`, `turma`, `aluno`, `matricula`) |
| `GET /ieducar/dados` | Dados de origem lidos do i-Educar |
| `GET /moodle/turmas/{id}` | Curso e participantes no Moodle de uma turma do i-Educar |
| `GET /health` | Verificação de vida |

Exemplos:

```bash
curl http://localhost:8090/saude
curl -X POST http://localhost:8090/sincronizacoes
curl http://localhost:8090/moodle/turmas/3
```

## 5. Roteiro de demonstração

1. **Origem no i-Educar.** Entre em http://localhost:8000 → *Escola → Cadastros → Turmas* (ou http://localhost:8000/intranet/educar_turma_lst.php) e mostre as turmas do ano e os alunos de uma turma.
2. **Moodle antes.** Em http://localhost:8080 → *Site administration → Courses → Manage courses and categories*. Se a primeira execução automática ainda não ocorreu, só existe a categoria padrão.
3. **Sincronizar.** No painel (http://localhost:8090) clique em *Sincronizar agora*. O relatório mostra 2 escolas, 21 turmas, 210 alunos e 210 matrículas criados.
4. **Resultado no Moodle.** Recarregue a gestão de cursos: as categorias das escolas e os cursos das turmas aparecem, cada um com o `idnumber` de origem (`ieducar-turma-3` etc.). Abra um curso → *Participants*: os alunos da turma estão inscritos como estudantes.
5. **Sem duplicação.** Clique de novo em *Sincronizar agora*: tudo aparece como *inalterado* e nada é enviado ao Moodle.
6. **Alterações na origem.** Faça mudanças no i-Educar pela interface (por exemplo, corrigir o nome de um aluno) ou rode o script que simula ações da secretaria:

   ```bash
   docker compose exec -T ieducar-db psql -U ieducar -d ieducar < scripts/cenarios_demo.sql
   ```

   Ele corrige um nome, transfere um aluno, remaneja outro de turma e coloca dois irmãos com o mesmo e-mail. Sincronize e mostre no Moodle: nome atualizado, inscrição do transferido **suspensa**, remanejado suspenso na turma antiga e ativo na nova, e o irmão com e-mail técnico (`alunoN@sem-email.invalid`).
7. **Falhas.** `docker compose stop moodle`, sincronize: a execução falha rapidamente e fica registrada. `docker compose start moodle` e sincronize de novo: retoma normalmente.
8. **Reconciliação.** Apague um curso no Moodle e rode `POST /sincronizacoes?completa=true` (ou aguarde a reconciliação automática): o curso volta com as inscrições.

## 6. Arquitetura

```
┌────────────── i-Educar ──────────────┐     ┌──── Integração (monólito) ────┐     ┌──────── Moodle ────────┐
│ ieducar-web (Nginx)                  │ GET │ API REST + painel             │POST │ moodle (Apache + PHP)  │
│ ieducar (PHP-FPM) ── ieducar-db (PG) │◄────┤ agendador → SyncService       ├────►│ moodle-db (PostgreSQL) │
│                   └─ ieducar-redis   │     │ mapper · domínio · SQLite     │     │                        │
└──────────────────────────────────────┘     └───────────────────────────────┘     └────────────────────────┘
                   └──────── tokens gerados no 1º boot (volume "tokens", só leitura para a integração) ────────┘
```

Estrutura do repositório:

```
docker-compose.yml          ambiente completo (7 serviços)
.env.example                portas, senhas e parâmetros
docker/
  ieducar/                  Dockerfile (PHP-FPM + Nginx), entrypoint (migrations, seed, token), nginx.conf
  moodle/                   Dockerfile sobre bitnamilegacy/moodle, entrypoint e setup_webservice.php
integracao/                 aplicação de integração (Python 3.13 + FastAPI)
  app/
    api/routes.py           endpoints REST e painel
    scheduler.py            agendamento periódico
    container.py            montagem das dependências
    sync/service.py         regras de sincronização (ordem, idempotência, falhas)
    sync/mapper.py          mapeamento i-Educar → Moodle
    domain.py               modelo canônico (Escola, Turma, Aluno, Matricula)
    ieducar/                interface FonteIEducar e implementação pela API REST
    moodle/client.py        cliente dos Web Services do Moodle
    storage.py              vínculos e histórico (SQLite)
  tests/                    54 testes automatizados
scripts/cenarios_demo.sql   alterações de exemplo para a demonstração
relatorio/                  relatório em LaTeX (modelo SBC) e PDF
```

Principais decisões (detalhadas no relatório):

- **Sem duplicidade:** cada registro no Moodle guarda o `idnumber` de origem (`ieducar-turma-12`) e é procurado por ele antes de ser criado; uma tabela local de vínculos com *hash* evita reenvios; uma trava impede execuções simultâneas (HTTP 409).
- **Somente o que mudou:** o *hash* dos dados de origem decide se algo precisa ser enviado.
- **Falhas isoladas:** o erro em um registro não interrompe os demais; registros que falharam são refeitos na execução seguinte; se um sistema estiver fora do ar, a execução para logo e é registrada.
- **Suspender em vez de remover:** transferências e remanejamentos suspendem a inscrição, preservando o histórico do aluno no Moodle.
- **Privilégio mínimo:** no Moodle, a integração usa um usuário de serviço com papel restrito às capacidades necessárias e um serviço externo com apenas 13 funções; no i-Educar, um token Sanctum.

## 7. Configuração

Variáveis do `.env` (todas têm valor padrão):

| Variável | Padrão | Descrição |
|---|---|---|
| `IEDUCAR_PORT` / `MOODLE_PORT` / `INTEGRACAO_PORT` | 8000 / 8080 / 8090 | Portas no computador |
| `MOODLE_ADMIN_PASSWORD` | `Moodle@2026` | Senha do `admin` do Moodle |
| `IEDUCAR_DEMO_DATA` | `true` | Carrega os dados de demonstração do i-Educar no primeiro boot |
| `IEDUCAR_REF` | commit fixo | Versão do i-Educar usada no build |
| `SYNC_ANO_LETIVO` | ano atual | Ano letivo sincronizado |
| `SYNC_INTERVALO_MINUTOS` | 5 | Intervalo da sincronização automática (0 desliga) |
| `SYNC_RECONCILIACAO_A_CADA` | 12 | A cada N execuções automáticas, faz uma reconciliação completa |

## 8. Testes

```bash
docker compose exec integracao pytest
```

São 54 testes: regras de sincronização (com um Moodle em memória que reproduz as restrições reais), clientes HTTP (com respostas reais capturadas da API do i-Educar), mapeamento e endpoints.

Para rodar fora do Docker:

```bash
cd integracao
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## 9. Relatório

O relatório está em `relatorio/relatorio.pdf`, com os fontes em LaTeX no modelo da SBC (`relatorio.tex`, `referencias.bib`, `sbc-template.sty`, `sbc.bst`, `caption2.sty` e `figuras/`). Para recompilar:

```bash
cd relatorio
latexmk -pdf relatorio.tex
```

No Overleaf, basta enviar a pasta `relatorio/` e compilar com pdfLaTeX.

## 10. Solução de problemas

| Sintoma | O que fazer |
|---|---|
| `integracao` não sobe | Ela espera o Moodle ficar *healthy*. Veja `docker compose logs moodle`; a instalação pode levar mais de 10 minutos na primeira vez. |
| Painel mostra *Token ausente* | Veja se existem `/shared/ieducar_token` e `/shared/moodle_token`: `docker compose exec integracao ls -l /shared`. Se o do Moodle faltar, rode `docker compose exec moodle php /opt/integracao/setup_webservice.php /shared/moodle_token`. |
| Token do i-Educar recusado | Gere outro: `docker compose exec ieducar sh -c "php artisan admin:token integracao-moodle --no-ansi \| tail -n1 \| tr -d ' ' > /shared/ieducar_token"`. |
| Porta em uso | Altere `IEDUCAR_PORT`, `MOODLE_PORT` ou `INTEGRACAO_PORT` no `.env`. |
| Quero recomeçar do zero | `docker compose down -v && docker compose up -d --build`. |
| Erro de memória no build do i-Educar | Aumente a memória do Docker Desktop (Settings → Resources) para 6 GB ou mais. |
