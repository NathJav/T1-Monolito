<?php
// Configura o Moodle para receber a integração com o i-Educar.
//
// O script é idempotente: pode ser executado quantas vezes for preciso.
//   1. Habilita os web services e o protocolo REST;
//   2. Cria o papel "integracao_ieducar" com apenas as capacidades necessárias;
//   3. Cria o usuário de serviço "integracao" e atribui o papel no contexto de sistema;
//   4. Cria o serviço externo "ieducar_integracao" com as funções usadas pela aplicação;
//   5. Gera (ou reutiliza) um token permanente e o grava no arquivo indicado.
//
// Uso: php setup_webservice.php [/caminho/do/arquivo/de/token]
// Variáveis: MOODLE_CONFIG (caminho do config.php), MOODLE_WS_USER, MOODLE_WS_TOKEN_FILE.

define('CLI_SCRIPT', true);

$configpath = getenv('MOODLE_CONFIG') ?: '/bitnami/moodle/config.php';
if (!file_exists($configpath)) {
    $configpath = '/opt/bitnami/moodle/config.php';
}
require($configpath);

require_once($CFG->libdir . '/clilib.php');
require_once($CFG->libdir . '/externallib.php');
require_once($CFG->dirroot . '/webservice/lib.php');
require_once($CFG->dirroot . '/user/lib.php');

use core_external\util as external_util;

// As ações são registradas em nome do administrador do site.
\core\session\manager::set_user(get_admin());

$tokenfile = $argv[1] ?? (getenv('MOODLE_WS_TOKEN_FILE') ?: '');
$username = getenv('MOODLE_WS_USER') ?: 'integracao';
$serviceshortname = 'ieducar_integracao';
$roleshortname = 'integracao_ieducar';

// Funções da API do Moodle usadas pela aplicação de integração.
$functions = [
    'core_webservice_get_site_info',
    'core_course_get_categories',
    'core_course_create_categories',
    'core_course_update_categories',
    'core_course_get_courses_by_field',
    'core_course_create_courses',
    'core_course_update_courses',
    'core_user_get_users_by_field',
    'core_user_create_users',
    'core_user_update_users',
    'core_enrol_get_enrolled_users',
    'enrol_manual_enrol_users',
    'enrol_manual_unenrol_users',
];

// Capacidades mínimas exigidas por essas funções.
$capabilities = [
    'webservice/rest:use',
    'moodle/category:manage',
    'moodle/category:viewhiddencategories',
    'moodle/course:create',
    'moodle/course:update',
    'moodle/course:view',
    'moodle/course:viewhiddencourses',
    'moodle/course:visibility',
    'moodle/course:viewparticipants',
    'moodle/course:enrolreview',
    'moodle/course:changeidnumber',
    'moodle/course:changefullname',
    'moodle/course:changeshortname',
    'moodle/course:changecategory',
    'moodle/user:create',
    'moodle/user:update',
    'moodle/user:viewdetails',
    'moodle/user:viewhiddendetails',
    'moodle/user:viewalldetails',
    'moodle/site:viewuseridentity',
    'moodle/site:accessallgroups',
    'moodle/role:assign',
    'enrol/manual:enrol',
    'enrol/manual:unenrol',
];

$systemcontext = context_system::instance();

// 1. Web services + REST.
set_config('enablewebservices', 1);
$protocols = array_filter(explode(',', $CFG->webserviceprotocols ?? ''));
if (!in_array('rest', $protocols)) {
    $protocols[] = 'rest';
    set_config('webserviceprotocols', implode(',', $protocols));
}
cli_writeln('[ok] Web services e protocolo REST habilitados');

// 2. Papel com as capacidades mínimas.
$role = $DB->get_record('role', ['shortname' => $roleshortname]);
if (!$role) {
    $roleid = create_role(
        'Integração i-Educar',
        $roleshortname,
        'Papel de serviço usado pela aplicação de integração i-Educar → Moodle.'
    );
    set_role_contextlevels($roleid, [CONTEXT_SYSTEM]);
    $role = $DB->get_record('role', ['id' => $roleid]);
}
foreach ($capabilities as $capability) {
    if (get_capability_info($capability)) {
        assign_capability($capability, CAP_ALLOW, $role->id, $systemcontext->id, true);
    }
}
// Permite que o papel de integração atribua o papel de estudante nas inscrições.
$studentrole = $DB->get_record('role', ['shortname' => 'student'], '*', MUST_EXIST);
if (!$DB->record_exists('role_allow_assign', ['roleid' => $role->id, 'allowassign' => $studentrole->id])) {
    core_role_set_assign_allowed($role->id, $studentrole->id);
}
$systemcontext->mark_dirty();
cli_writeln("[ok] Papel '{$roleshortname}' (id {$role->id}) configurado");

// 3. Usuário de serviço.
$user = $DB->get_record('user', ['username' => $username, 'mnethostid' => $CFG->mnet_localhost_id, 'deleted' => 0]);
if (!$user) {
    $userid = user_create_user((object) [
        'username' => $username,
        'auth' => 'manual',
        'password' => random_string(24) . 'Aa1!',
        'firstname' => 'Serviço',
        'lastname' => 'Integração i-Educar',
        'email' => $username . '@integracao.local',
        'confirmed' => 1,
        'mnethostid' => $CFG->mnet_localhost_id,
    ], true, false);
    $user = $DB->get_record('user', ['id' => $userid]);
}
role_assign($role->id, $user->id, $systemcontext->id);
cli_writeln("[ok] Usuário de serviço '{$username}' (id {$user->id})");

// 4. Serviço externo com as funções necessárias.
$wsmanager = new webservice();
$service = $wsmanager->get_external_service_by_shortname($serviceshortname);
if (!$service) {
    $serviceid = $wsmanager->add_external_service((object) [
        'name' => 'Integração i-Educar',
        'shortname' => $serviceshortname,
        'enabled' => 1,
        'restrictedusers' => 1,
        'downloadfiles' => 0,
        'uploadfiles' => 0,
    ]);
    $service = $wsmanager->get_external_service_by_id($serviceid);
}
foreach ($functions as $function) {
    if (!$wsmanager->service_function_exists($function, $service->id)) {
        $wsmanager->add_external_function_to_service($function, $service->id);
    }
}
if (!$DB->record_exists('external_services_users', ['externalserviceid' => $service->id, 'userid' => $user->id])) {
    $wsmanager->add_ws_authorised_user((object) [
        'externalserviceid' => $service->id,
        'userid' => $user->id,
    ]);
}
cli_writeln("[ok] Serviço externo '{$serviceshortname}' com " . count($functions) . ' funções');

// 5. Token permanente (reutiliza se já existir).
$token = $DB->get_field_select(
    'external_tokens',
    'token',
    'externalserviceid = :sid AND userid = :uid AND tokentype = :type AND (validuntil = 0 OR validuntil > :now)',
    ['sid' => $service->id, 'uid' => $user->id, 'type' => EXTERNAL_TOKEN_PERMANENT, 'now' => time()],
    IGNORE_MULTIPLE
);
if (!$token) {
    $token = external_util::generate_token(
        EXTERNAL_TOKEN_PERMANENT,
        $service,
        $user->id,
        $systemcontext,
        0,
        '',
        'Integração i-Educar'
    );
}

if ($tokenfile) {
    @mkdir(dirname($tokenfile), 0775, true);
    file_put_contents($tokenfile, $token);
    @chmod($tokenfile, 0644);
    cli_writeln("[ok] Token gravado em {$tokenfile}");
} else {
    cli_writeln("[ok] Token: {$token}");
}
