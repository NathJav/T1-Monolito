# Moodle a partir da imagem bitnamilegacy/moodle, com a configuração dos web services
# REST feita automaticamente depois da instalação.
ARG MOODLE_IMAGE=bitnamilegacy/moodle:5.0.2-debian-12-r2
FROM ${MOODLE_IMAGE}

COPY setup_webservice.php /opt/integracao/setup_webservice.php
COPY entrypoint.sh /opt/integracao/entrypoint.sh
RUN chmod +x /opt/integracao/entrypoint.sh

# Redefinir o ENTRYPOINT apaga o CMD herdado; ele é declarado de novo para manter o
# fluxo original (o entrypoint da Bitnami só instala o Moodle quando recebe run.sh).
ENTRYPOINT ["/opt/integracao/entrypoint.sh"]
CMD ["/opt/bitnami/scripts/moodle/run.sh"]
