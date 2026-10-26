# i-Educar construído a partir do código-fonte oficial (github.com/portabilis/i-educar).
#
# Dois alvos no mesmo Dockerfile:
#   php -> PHP-FPM com o código e as dependências (composer)
#   web -> Nginx servindo os arquivos públicos e encaminhando o PHP para o alvo "php"
#
# A versão do i-Educar é fixada por commit para que o ambiente seja reprodutível.

ARG PHP_VERSION=8.5

FROM composer:2 AS composer

FROM php:${PHP_VERSION}-fpm-alpine AS php

ARG IEDUCAR_REPO=https://github.com/portabilis/i-educar.git
ARG IEDUCAR_REF=7903a3efc7689942ee700007f86ed581023aad27

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_PROCESS_TIMEOUT=1800

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

# Mesmas dependências e extensões do docker/php/Dockerfile oficial do i-Educar.
RUN apk add --no-cache \
        git unzip libpng-dev libzip-dev linux-headers postgresql-dev postgresql-client \
    && apk add --no-cache --virtual .build-deps $PHPIZE_DEPS \
    && pecl install redis \
    && docker-php-ext-enable redis \
    && docker-php-ext-install bcmath ftp gd pcntl pdo pdo_pgsql pgsql sockets zip \
    && apk del .build-deps

RUN { \
      echo "memory_limit=512M"; \
      echo "upload_max_filesize=100M"; \
      echo "post_max_size=100M"; \
      echo "date.timezone=America/Sao_Paulo"; \
    } > /usr/local/etc/php/conf.d/ieducar.ini

WORKDIR /var/www/ieducar

# Código do i-Educar na versão fixada.
RUN git init -q . \
    && git remote add origin "${IEDUCAR_REPO}" \
    && git fetch -q --depth 1 origin "${IEDUCAR_REF}" \
    && git checkout -q FETCH_HEAD \
    && rm -rf .git

# Dependências PHP (inclui as de desenvolvimento, pois o seeder de demonstração usa o Faker).
RUN cp .env.example .env \
    && composer install --no-interaction --prefer-dist --no-progress --optimize-autoloader
RUN composer plug-and-play --no-interaction || echo "plug-and-play: nenhum pacote adicional"

# O PHP-FPM limpa as variáveis de ambiente por padrão; o i-Educar precisa delas
# (DB_HOST, REDIS_HOST, APP_KEY...) para não cair nos valores do .env de exemplo.
RUN echo "clear_env = no" >> /usr/local/etc/php-fpm.d/zz-docker.conf

COPY entrypoint.sh /usr/local/bin/ieducar-entrypoint
RUN chmod +x /usr/local/bin/ieducar-entrypoint

EXPOSE 9000
ENTRYPOINT ["ieducar-entrypoint"]
CMD ["php-fpm"]


FROM nginx:1.30-alpine AS web

COPY --from=php /var/www/ieducar/public /var/www/ieducar/public
COPY nginx.conf /etc/nginx/conf.d/default.conf
