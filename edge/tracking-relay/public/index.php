<?php
declare(strict_types=1);
ini_set('display_errors', '0');
ini_set('log_errors', '1');

/* Green Diamond first-party tracking relay. No CRM routes or upstream proxy. */

const SESSION_KEYS = ['site_code', 'visitor_id', 'session_id', 'landing_url', 'referrer', 'utm'];
const EVENT_KEYS = ['site_code', 'session_id', 'event_id', 'event_type', 'page_url', 'metadata', 'timestamp'];
const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
const META_KEYS = ['click_id', 'language', 'viewport_width', 'viewport_height', 'timezone'];
const QUERY_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'fbclid'];

function respond(int $status, ?array $body = null, array $headers = []): never {
    http_response_code($status);
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    foreach ($headers as $key => $value) header($key . ': ' . $value);
    if ($body !== null) {
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($body, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    }
    exit;
}

function fail(int $status, string $code): never {
    respond($status, ['ok' => false, 'error' => $code]);
}

function config(): array {
    $path = getenv('GD_EDGE_CONFIG_FILE') ?: (__DIR__ . '/../../../shared/config.php');
    if (!is_file($path)) fail(503, 'edge_unavailable');
    $cfg = require $path;
    if (!is_array($cfg)) fail(503, 'edge_unavailable');
    if (!empty($cfg['secret_file']) && is_file((string)$cfg['secret_file'])) {
        $cfg['secret'] = trim((string)file_get_contents((string)$cfg['secret_file']));
    }
    if (strlen((string)($cfg['secret'] ?? '')) < 32) fail(503, 'edge_unavailable');
    return $cfg;
}

function request_path(): string {
    $path = parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH);
    return is_string($path) ? '/' . ltrim($path, '/') : '/';
}

function cors_headers(string $origin): array {
    return [
        'Access-Control-Allow-Origin' => $origin,
        'Access-Control-Allow-Methods' => 'POST, OPTIONS',
        'Access-Control-Allow-Headers' => 'Content-Type',
        'Access-Control-Max-Age' => '600',
        'Vary' => 'Origin',
    ];
}

function read_json(int $maxBytes): array {
    $length = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($length > $maxBytes) fail(413, 'payload_too_large');
    $raw = file_get_contents('php://input', false, null, 0, $maxBytes + 1);
    if ($raw === false || strlen($raw) > $maxBytes) fail(413, 'payload_too_large');
    try { $data = json_decode($raw, true, 64, JSON_THROW_ON_ERROR); }
    catch (Throwable $e) { fail(400, 'invalid_json'); }
    if (!is_array($data) || array_is_list($data)) fail(400, 'invalid_json');
    return [$data, $raw];
}

function sensitive_key(string $key): bool {
    return preg_match('/pass|passwd|secret|token|authorization|cookie|card|cvv|rut|email|phone|telefono|message|body/i', $key) === 1;
}

function reject_sensitive(mixed $value, int $depth = 0): void {
    if ($depth > 12) fail(422, 'payload_too_deep');
    if (!is_array($value)) return;
    if (count($value) > 80) fail(422, 'payload_too_wide');
    foreach ($value as $key => $item) {
        if (is_string($key) && sensitive_key($key)) fail(422, 'sensitive_field');
        reject_sensitive($item, $depth + 1);
    }
}

function allow_keys(array $payload, array $allowed): void {
    foreach (array_keys($payload) as $key) if (!in_array($key, $allowed, true)) fail(422, 'unknown_field');
    reject_sensitive($payload);
    if (isset($payload['utm'])) {
        if (!is_array($payload['utm']) || array_is_list($payload['utm'])) fail(422, 'invalid_utm');
        foreach (array_keys($payload['utm']) as $key) if (!in_array($key, UTM_KEYS, true)) fail(422, 'unknown_utm');
    }
    if (isset($payload['metadata'])) {
        if (!is_array($payload['metadata']) || array_is_list($payload['metadata'])) fail(422, 'invalid_metadata');
        foreach (array_keys($payload['metadata']) as $key) if (!in_array($key, META_KEYS, true)) fail(422, 'unknown_metadata');
    }
}

function uuid_value(mixed $value): string {
    $text = strtolower(trim((string)$value));
    if (!preg_match('/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/', $text)) fail(422, 'invalid_identifier');
    return $text;
}

function safe_text(mixed $value, int $max): ?string {
    if ($value === null || $value === '') return null;
    $text = trim((string)$value);
    if (strlen($text) > $max || preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F]/', $text)) fail(422, 'invalid_value');
    return $text;
}

function minimize_url(mixed $value, bool $marketing = true): ?string {
    if ($value === null || $value === '') return null;
    $raw = safe_text($value, 4096);
    $parts = parse_url((string)$raw);
    if (!is_array($parts) || !in_array(strtolower((string)($parts['scheme'] ?? '')), ['http', 'https'], true) || empty($parts['host'])) fail(422, 'invalid_url');
    $scheme = strtolower((string)$parts['scheme']);
    $host = strtolower((string)$parts['host']);
    $port = isset($parts['port']) && !(($scheme === 'https' && $parts['port'] === 443) || ($scheme === 'http' && $parts['port'] === 80)) ? ':' . (int)$parts['port'] : '';
    $path = (string)($parts['path'] ?? '/');
    if ($path === '') $path = '/';
    $query = [];
    if ($marketing && isset($parts['query'])) {
        parse_str((string)$parts['query'], $input);
        foreach (QUERY_KEYS as $key) {
            if (!isset($input[$key]) || is_array($input[$key])) continue;
            $query[$key] = substr((string)$input[$key], 0, 500);
        }
    }
    $result = $scheme . '://' . $host . $port . $path . ($query ? '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986) : '');
    return substr($result, 0, 2048);
}

function normalized_host(string $url): string {
    return strtolower(preg_replace('/^www\./i', '', (string)(parse_url($url, PHP_URL_HOST) ?: '')));
}

function validate_origin_site(array $cfg, array $payload): array {
    $origin = trim((string)($_SERVER['HTTP_ORIGIN'] ?? ''));
    if (!preg_match('#^https://[^/]+$#i', $origin)) fail(403, 'origin_denied');
    $code = strtoupper(trim((string)($payload['site_code'] ?? '')));
    if (!preg_match('/^[A-Z0-9_]{2,16}$/', $code)) fail(422, 'invalid_site');
    $sites = $cfg['sites'] ?? [];
    if (!isset($sites[$code]) || !in_array($origin, $sites[$code], true)) fail(403, 'origin_site_mismatch');
    return [$origin, $code, normalized_host($origin)];
}

function db(array $cfg): PDO {
    $path = (string)($cfg['db_path'] ?? '');
    if ($path === '') fail(503, 'edge_unavailable');
    $pdo = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]);
    $pdo->exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;');
    $pdo->exec('CREATE TABLE IF NOT EXISTS queue (edge_id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL,site_code TEXT NOT NULL,event_type TEXT NOT NULL,payload_sanitized TEXT NOT NULL,origin TEXT NOT NULL,user_agent TEXT,received_at INTEGER NOT NULL,lease_id TEXT,lease_until INTEGER,acked_at INTEGER,UNIQUE(event_type,event_id));');
    $pdo->exec('CREATE INDEX IF NOT EXISTS ix_queue_delivery ON queue(acked_at,lease_until,edge_id);');
    $pdo->exec('CREATE INDEX IF NOT EXISTS ix_queue_received ON queue(received_at);');
    $pdo->exec('CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY,expires_at INTEGER NOT NULL);');
    $pdo->exec('CREATE INDEX IF NOT EXISTS ix_nonces_expiry ON nonces(expires_at);');
    $pdo->exec('CREATE TABLE IF NOT EXISTS rate_limits (bucket TEXT PRIMARY KEY,hits INTEGER NOT NULL,expires_at INTEGER NOT NULL);');
    return $pdo;
}

function rate_limit(PDO $pdo, array $cfg, string $origin, string $site): void {
    $limit = max(10, min(2000, (int)($cfg['rate_limit_per_minute'] ?? 180)));
    $source = substr((string)($_SERVER['REMOTE_ADDR'] ?? 'unknown'), 0, 64);
    $minute = intdiv(time(), 60);
    $bucket = hash('sha256', $source . '|' . $origin . '|' . $site . '|' . $minute);
    $pdo->beginTransaction();
    $stmt = $pdo->prepare('INSERT INTO rate_limits(bucket,hits,expires_at) VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET hits=hits+1');
    $stmt->execute([$bucket, time() + 180]);
    $check = $pdo->prepare('SELECT hits FROM rate_limits WHERE bucket=?'); $check->execute([$bucket]);
    $hits = (int)$check->fetchColumn();
    if (random_int(1, 100) === 1) $pdo->prepare('DELETE FROM rate_limits WHERE expires_at<?')->execute([time()]);
    $pdo->commit();
    if ($hits > $limit) fail(429, 'rate_limited');
}

function public_collect(array $cfg, string $path): never {
    [$payload] = read_json((int)($cfg['max_body_bytes'] ?? 16384));
    $isSession = $path === '/v1/session';
    allow_keys($payload, $isSession ? SESSION_KEYS : EVENT_KEYS);
    [$origin, $site, $host] = validate_origin_site($cfg, $payload);
    if ($isSession) {
        $payload['visitor_id'] = uuid_value($payload['visitor_id'] ?? null);
        $payload['session_id'] = uuid_value($payload['session_id'] ?? null);
        $payload['landing_url'] = minimize_url($payload['landing_url'] ?? null, true);
        if (normalized_host((string)$payload['landing_url']) !== $host) fail(422, 'page_origin_mismatch');
        $payload['referrer'] = minimize_url($payload['referrer'] ?? null, false);
        foreach (($payload['utm'] ?? []) as $key => $value) $payload['utm'][$key] = safe_text($value, 200);
        $eventType = 'session'; $eventId = $payload['session_id'];
    } else {
        $payload['session_id'] = uuid_value($payload['session_id'] ?? null);
        $payload['event_id'] = uuid_value($payload['event_id'] ?? null);
        $payload['event_type'] = strtolower((string)safe_text($payload['event_type'] ?? null, 60));
        if (!in_array($payload['event_type'], ['session_start', 'page_view', 'click_whatsapp', 'form_start', 'form_submit', 'lead_created'], true)) fail(422, 'unsupported_event');
        $payload['page_url'] = minimize_url($payload['page_url'] ?? null, true);
        if ($payload['page_url'] && normalized_host($payload['page_url']) !== $host) fail(422, 'page_origin_mismatch');
        if (isset($payload['timestamp'])) $payload['timestamp'] = safe_text($payload['timestamp'], 40);
        foreach (($payload['metadata'] ?? []) as $key => $value) $payload['metadata'][$key] = is_scalar($value) ? safe_text($value, 200) : null;
        $eventType = 'event'; $eventId = $payload['event_id'];
    }
    $pdo = db($cfg); rate_limit($pdo, $cfg, $origin, $site);
    $stmt = $pdo->prepare('INSERT OR IGNORE INTO queue(event_id,site_code,event_type,payload_sanitized,origin,user_agent,received_at) VALUES(?,?,?,?,?,?,?)');
    $ua = substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 300);
    $stmt->execute([$eventId, $site, $eventType, json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), $origin, $ua, time()]);
    respond(204, null, cors_headers($origin));
}

function require_hmac(array $cfg, string $method, string $path, string $raw, PDO $pdo): void {
    $timestamp = (string)($_SERVER['HTTP_X_GD_TIMESTAMP'] ?? '');
    $nonce = strtolower((string)($_SERVER['HTTP_X_GD_NONCE'] ?? ''));
    $signature = strtolower((string)($_SERVER['HTTP_X_GD_SIGNATURE'] ?? ''));
    $now = time(); $window = max(30, min(900, (int)($cfg['signature_window_seconds'] ?? 300)));
    if (!ctype_digit($timestamp) || abs($now - (int)$timestamp) > $window) fail(401, 'invalid_signature');
    if (!preg_match('/^[0-9a-f]{32,64}$/', $nonce) || !preg_match('/^[0-9a-f]{64}$/', $signature)) fail(401, 'invalid_signature');
    $canonical = $timestamp . "\n" . $nonce . "\n" . strtoupper($method) . "\n" . $path . "\n" . hash('sha256', $raw);
    $expected = hash_hmac('sha256', $canonical, (string)$cfg['secret']);
    if (!hash_equals($expected, $signature)) fail(401, 'invalid_signature');
    try {
        $pdo->beginTransaction();
        $pdo->prepare('DELETE FROM nonces WHERE expires_at<?')->execute([$now]);
        $pdo->prepare('INSERT INTO nonces(nonce,expires_at) VALUES(?,?)')->execute([$nonce, $now + $window]);
        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        fail(409, 'replayed_request');
    }
}

function metrics(PDO $pdo): array {
    $row = $pdo->query('SELECT COUNT(*) AS queue_depth,MIN(received_at) AS oldest,MAX(received_at) AS last_event FROM queue WHERE acked_at IS NULL')->fetch();
    return [
        'queue_depth' => (int)($row['queue_depth'] ?? 0),
        'oldest_unacked_age' => $row['oldest'] ? max(0, time() - (int)$row['oldest']) : 0,
        'last_event_at' => $row['last_event'] ? gmdate('c', (int)$row['last_event']) : null,
    ];
}

function internal_pull(array $cfg, PDO $pdo, array $body): never {
    $limit = max(1, min((int)($cfg['max_batch_size'] ?? 500), (int)($body['limit'] ?? 500)));
    $leaseSeconds = max(30, min(1800, (int)($cfg['lease_seconds'] ?? 180)));
    $lease = bin2hex(random_bytes(16)); $now = time();
    $pdo->exec('BEGIN IMMEDIATE');
    $stmt = $pdo->prepare('SELECT edge_id,event_id,site_code,event_type,payload_sanitized,origin,user_agent,received_at FROM queue WHERE acked_at IS NULL AND (lease_until IS NULL OR lease_until<?) ORDER BY edge_id LIMIT ?');
    $stmt->bindValue(1, $now, PDO::PARAM_INT); $stmt->bindValue(2, $limit, PDO::PARAM_INT); $stmt->execute();
    $items = $stmt->fetchAll();
    if ($items) {
        $ids = array_column($items, 'edge_id');
        $marks = implode(',', array_fill(0, count($ids), '?'));
        $update = $pdo->prepare("UPDATE queue SET lease_id=?,lease_until=? WHERE edge_id IN ($marks) AND acked_at IS NULL");
        $update->execute(array_merge([$lease, $now + $leaseSeconds], $ids));
    }
    $ackedRetention = max(3600, (int)($cfg['acked_retention_seconds'] ?? 604800));
    $pdo->prepare('DELETE FROM queue WHERE edge_id IN (SELECT edge_id FROM queue WHERE acked_at IS NOT NULL AND acked_at<? ORDER BY edge_id LIMIT 1000)')->execute([$now - $ackedRetention]);
    $pdo->exec('COMMIT');
    foreach ($items as &$item) $item['payload'] = json_decode($item['payload_sanitized'], true, 64, JSON_THROW_ON_ERROR);
    unset($item);
    respond(200, ['ok' => true, 'lease_id' => $items ? $lease : null, 'items' => $items, 'metrics' => metrics($pdo)]);
}

function internal_ack(PDO $pdo, array $body): never {
    $lease = strtolower((string)($body['lease_id'] ?? ''));
    $ids = $body['edge_ids'] ?? [];
    if (!preg_match('/^[0-9a-f]{32}$/', $lease) || !is_array($ids) || count($ids) > 500) fail(422, 'invalid_ack');
    $ids = array_values(array_unique(array_filter(array_map('intval', $ids), fn($id) => $id > 0)));
    if (!$ids) respond(200, ['ok' => true, 'acked' => 0, 'metrics' => metrics($pdo)]);
    $marks = implode(',', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare("UPDATE queue SET acked_at=?,lease_until=NULL WHERE lease_id=? AND edge_id IN ($marks) AND acked_at IS NULL");
    $stmt->execute(array_merge([time(), $lease], $ids));
    respond(200, ['ok' => true, 'acked' => $stmt->rowCount(), 'metrics' => metrics($pdo)]);
}

$cfg = config();
$path = request_path();
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$public = ['/v1/session', '/v1/event'];
$internal = ['/internal/v1/pull', '/internal/v1/ack'];

if (in_array($path, $public, true)) {
    if ($method === 'OPTIONS') {
        $origin = trim((string)($_SERVER['HTTP_ORIGIN'] ?? ''));
        $allowed = false; foreach (($cfg['sites'] ?? []) as $origins) if (in_array($origin, $origins, true)) $allowed = true;
        if (!$allowed) fail(403, 'origin_denied');
        respond(204, null, cors_headers($origin));
    }
    if ($method !== 'POST') fail(405, 'method_not_allowed');
    public_collect($cfg, $path);
}

if (in_array($path, $internal, true)) {
    if ($method !== 'POST') fail(405, 'method_not_allowed');
    [$body, $raw] = read_json((int)($cfg['max_internal_body_bytes'] ?? 65536));
    $pdo = db($cfg); require_hmac($cfg, $method, $path, $raw, $pdo);
    if ($path === '/internal/v1/pull') internal_pull($cfg, $pdo, $body);
    internal_ack($pdo, $body);
}

fail(404, 'not_found');
