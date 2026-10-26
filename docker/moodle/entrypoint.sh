#!/bin/bash
# Envolve o entrypoint original da Bitnami.
#
# Em segundo plano, espera o Moodle terminar a instalação e começar a responder
# (o servidor web só sobe depois do setup da Bitnami) e então executa
# setup_webservice.php, que é idempotente: habilita REST, cria o papel, o usuário
# de serviço, o serviço externo e grava o token em /shared/moodle_token.
set -e

TOKEN_FILE="${MOODLE_WS_TOKEN_FILE:-/shared/moodle_token}"
PORTA="${APACHE_HTTP_PORT_NUMBER:-8080}"

(
  for tentativa in $(seq 1 180); do
    if php -r "exit(@file_get_contents('http://127.0.0.1:${PORTA}/login/index.php') === false ? 1 : 0);" 2>/dev/null; then
      if php /opt/integracao/setup_webservice.php "$TOKEN_FILE"; then
        echo "[integracao] Web services do Moodle configurados."
        exit 0
      fi
    fi
    sleep 10
  done
  echo "[integracao] ERRO: não foi possível configurar os web services do Moodle." >&2
) &

exec /opt/bitnami/scripts/moodle/entrypoint.sh "$@"
