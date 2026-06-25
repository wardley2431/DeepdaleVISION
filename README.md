# ClassCast

ClassCast is a standalone, one-way classroom screen-sharing application. A
teacher creates a temporary six-digit PIN and publishes one browser screen
track. Up to 12 students enter the PIN and watch without publishing camera,
microphone, or screen data.

The application server handles PINs and short-lived access tokens. LiveKit acts
as the SFU, so the teacher uploads one stream rather than one stream per student.

## What Is Included

- Six-digit temporary room PINs
- Private host keys that never appear in the URL
- Viewer-only LiveKit access tokens
- A 12-viewer admission limit
- Viewer heartbeat cleanup
- 720p, 15 FPS, video-only screen sharing
- Separate teacher preview and student viewing interfaces
- Docker Compose configuration for the app and a local LiveKit server
- No Python package dependencies

## Start With Docker

Docker is the intended way to run the complete local stack.

1. Copy `.env.example` to `.env`.
2. For same-computer testing, leave `LIVEKIT_NODE_IP=127.0.0.1`.
3. Start the stack:

```powershell
docker compose up --build
```

Open:

```text
http://localhost:8080
```

The teacher can create a room in one browser window and join it in a second
window using the displayed PIN.

## Raspberry Pi Quick Start

On the Pi, install Docker and the Compose plugin first. Then clone or pull this
repository and start the stack:

```bash
cd ~/classcast-screen-share
cp .env.example .env
nano .env
docker compose up --build -d
```

Set `LIVEKIT_NODE_IP` in `.env` to the Pi's LAN address:

```text
LIVEKIT_NODE_IP=192.168.1.50
```

Find the Pi's LAN address with:

```bash
hostname -I
```

Students open:

```text
http://PI_LAN_IP:8080
```

The teacher should open the host page on the Pi itself at
`http://localhost:8080`, or use HTTPS if the teacher is sharing from another
machine. Browsers block screen sharing on ordinary LAN HTTP addresses because
screen capture requires a secure context.

Useful Pi commands:

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

For a closed network with no internet access, load the Docker images onto the Pi
before running Compose:

```bash
docker save python:3.13-slim livekit/livekit-server:v1.12.0 -o classcast-images.tar
docker load -i classcast-images.tar
```

## Classroom LAN

Set `LIVEKIT_NODE_IP` in `.env` to the Docker host's LAN address:

```text
LIVEKIT_NODE_IP=192.168.1.50
```

Students then open:

```text
http://192.168.1.50:8080
```

The teacher should open `http://localhost:8080` on the Docker host. Browsers
allow screen capture on localhost, but screen capture from a normal LAN HTTP
address is blocked because `getDisplayMedia()` requires a secure context.

Allow these inbound ports through the host firewall:

- TCP `8080` for the web app
- TCP `7880` for LiveKit signaling
- TCP `7881` for WebRTC fallback
- UDP `50000-50100` for WebRTC media

## Production Deployment

For internet access, use HTTPS for the app and WSS for LiveKit. The quickest
route is:

1. Deploy this app container behind an HTTPS reverse proxy.
2. Use LiveKit Cloud, or follow LiveKit's production VM deployment guide.
3. Set `LIVEKIT_URL` to the LiveKit WSS endpoint.
4. Set matching, randomly generated `LIVEKIT_API_KEY` and
   `LIVEKIT_API_SECRET` values.

Do not deploy the example development credentials publicly.

When using LiveKit Cloud, only the `app` service is required. Remove
`depends_on: livekit`, do not start the `livekit` service, and configure:

```text
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret
```

## Run the App Server Without Docker

This starts the PIN/token web server only; LiveKit must already be running:

```powershell
.\start.ps1
```

Then open `http://127.0.0.1:8080`.

## Tests

From this directory:

```powershell
python -m unittest discover -s tests -v
```

The tests cover room creation, host authentication, viewer limits and expiry,
viewer-only token grants, and the main HTTP API lifecycle.

## Operational Notes

- Rooms expire after four hours.
- Viewer reservations expire after 45 seconds without a heartbeat.
- Access tokens expire after two hours or when the room expires.
- Session state is held in memory, so restarting the app invalidates active
  PINs. This is suitable for a single classroom instance.
- Video is forwarded by LiveKit and is not recorded or stored by this app.
- The bundled browser SDK is pinned to LiveKit client `2.19.2`.
