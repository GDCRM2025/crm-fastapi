<?php
return [
    'secret_file' => '/absolute/protected/path/tracking_edge_secret',
    'db_path' => '/absolute/persistent/path/tracking-edge.sqlite3',
    'sites' => [
        'CAM' => ['https://carritoscamaleon.cl', 'https://www.carritoscamaleon.cl'],
        'EXP' => ['https://carritosexpress.cl', 'https://www.carritosexpress.cl'],
        'GOU' => ['https://carritosgourmet.cl', 'https://www.carritosgourmet.cl'],
        'DEL' => ['https://carritosdelsabor.cl', 'https://www.carritosdelsabor.cl'],
    ],
    'rate_limit_per_minute' => 180,
    'max_body_bytes' => 16384,
    'max_batch_size' => 500,
    'lease_seconds' => 180,
    'signature_window_seconds' => 300,
    'acked_retention_seconds' => 604800,
];
