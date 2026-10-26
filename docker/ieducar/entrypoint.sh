#!/bin/sh
# Prepara o i-Educar no primeiro boot e gera o token da API para a integração.
#
#   1. Aguarda o PostgreSQL;
#   2. Gera e guarda a APP_KEY (volume ieducar_state);
#   3. Roda as migrations (idempotente) e, só na primeira vez, os seeders
#      padrão e, opcionalmente, o DemoSeeder oficial do i-Educar;
#   4. Gera um token Sanctum (php artisan admin:token) e grava em /shared,
#      volume lido pela aplicação de integração.
set -e

cd /var/www/ieducar
STATE=/state
SHARED=/shared
mkdir -p "$STATE" "$SHARED"
rm -f "$STATE/ready"

echo "[ieducar] aguardando o banco ${DB_HOST}:${DB_PORT:-5432}..."
until pg_isready -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USERNAME" -q; do
  sleep 2
done

if [ -z "$APP_KEY" ]; then
  if [ ! -s "$STATE/app_key" ]; then
    php artisan key:generate --show --no-ansi > "$STATE/app_key"
  fi
  APP_KEY="$(cat "$STATE/app_key")"
  export APP_KEY
fi

php artisan storage:link --no-ansi >/dev/null 2>&1 || true

echo "[ieducar] aplicando migrations..."
php artisan migrate --force --no-ansi

if [ ! -f "$STATE/seeded" ]; then
  echo "[ieducar] carregando dados padrão (usuário admin / 123456789)..."
  php artisan db:seed --force --no-ansi
  if [ "${IEDUCAR_DEMO_DATA:-true}" = "true" ]; then
    echo "[ieducar] carregando dados de demonstração (DemoSeeder)..."
    php artisan db:seed --class=DemoSeeder --force --no-ansi
  fi
  touch "$STATE/seeded"
fi

if [ ! -s "$SHARED/ieducar_token" ]; then
  echo "[ieducar] gerando token da API para a integração..."
  php artisan admin:token integracao-moodle --no-ansi | tail -n 1 | tr -d ' \r' > "$SHARED/ieducar_token"
  chmod 644 "$SHARED/ieducar_token"
fi

chown -R www-data:www-data storage bootstrap/cache
touch "$STATE/ready"
echo "[ieducar] pronto."

exec "$@"
