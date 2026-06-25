$ErrorActionPreference = "Stop"

$env:APP_HOST = if ($env:APP_HOST) { $env:APP_HOST } else { "127.0.0.1" }
$env:APP_PORT = if ($env:APP_PORT) { $env:APP_PORT } else { "8080" }
$env:LIVEKIT_URL = if ($env:LIVEKIT_URL) { $env:LIVEKIT_URL } else { "ws://127.0.0.1:7880" }
$env:LIVEKIT_API_KEY = if ($env:LIVEKIT_API_KEY) { $env:LIVEKIT_API_KEY } else { "devkey" }
$env:LIVEKIT_API_SECRET = if ($env:LIVEKIT_API_SECRET) {
    $env:LIVEKIT_API_SECRET
} else {
    "devsecretdevsecretdevsecretdevsecret"
}

python "$PSScriptRoot\server.py"
