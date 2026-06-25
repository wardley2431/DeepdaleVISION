const views = {
  landing: document.querySelector("#landingView"),
  host: document.querySelector("#hostView"),
  viewer: document.querySelector("#viewerView"),
};

const hostForm = document.querySelector("#hostForm");
const joinForm = document.querySelector("#joinForm");
const hostNameInput = document.querySelector("#hostName");
const studentNameInput = document.querySelector("#studentName");
const pinInput = document.querySelector("#pinInput");
const hostPin = document.querySelector("#hostPin");
const viewerPin = document.querySelector("#viewerPin");
const viewerCount = document.querySelector("#viewerCount");
const viewerLimit = document.querySelector("#viewerLimit");
const hostConnection = document.querySelector("#hostConnection");
const viewerConnection = document.querySelector("#viewerConnection");
const shareButton = document.querySelector("#shareButton");
const stopShareButton = document.querySelector("#stopShareButton");
const copyPinButton = document.querySelector("#copyPinButton");
const endSessionButton = document.querySelector("#endSessionButton");
const leaveButton = document.querySelector("#leaveButton");
const hostVideo = document.querySelector("#hostVideo");
const viewerVideo = document.querySelector("#viewerVideo");
const hostPlaceholder = document.querySelector("#hostPlaceholder");
const viewerPlaceholder = document.querySelector("#viewerPlaceholder");
const viewerPlaceholderTitle = document.querySelector("#viewerPlaceholderTitle");
const viewerPlaceholderText = document.querySelector("#viewerPlaceholderText");
const hostLiveBadge = document.querySelector("#hostLiveBadge");
const toast = document.querySelector("#toast");

const state = {
  config: null,
  role: null,
  room: null,
  hostSession: null,
  viewerSession: null,
  heartbeatTimer: null,
  statusTimer: null,
  ending: false,
};

function showView(name) {
  Object.entries(views).forEach(([key, element]) => {
    element.classList.toggle("hidden", key !== name);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showToast(message, tone = "error") {
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 4200);
}

function setButtonBusy(button, busy, busyLabel) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

function normalizePin(value) {
  return String(value || "").replace(/\D/g, "").slice(0, 6);
}

function formatPin(pin) {
  const value = normalizePin(pin);
  return value.length > 3 ? `${value.slice(0, 3)} ${value.slice(3)}` : value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
    keepalive: Boolean(options.keepalive),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}

function livekitUrl() {
  const configured = state.config.livekitUrl;
  if (configured && configured !== "auto") return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:7880`;
}

function requireLiveKit() {
  if (!window.LivekitClient) {
    throw new Error("The LiveKit browser library could not be loaded.");
  }
  return window.LivekitClient;
}

function createRoom() {
  const { Room } = requireLiveKit();
  return new Room({
    adaptiveStream: true,
    dynacast: true,
    disconnectOnPageLeave: true,
  });
}

function saveSession(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function loadSession(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

function clearSession(key) {
  localStorage.removeItem(key);
}

async function connectHost(session) {
  const { RoomEvent, Track } = requireLiveKit();
  const tokenData = await api("/api/host/token", {
    method: "POST",
    body: {
      pin: session.pin,
      hostKey: session.hostKey,
      name: session.hostName,
    },
  });

  if (state.room) await state.room.disconnect();
  const room = createRoom();
  state.room = room;

  const updateCount = () => {
    viewerCount.textContent = String(room.remoteParticipants.size);
  };

  room.on(RoomEvent.ParticipantConnected, updateCount);
  room.on(RoomEvent.ParticipantDisconnected, updateCount);
  room.on(RoomEvent.Reconnecting, () => {
    hostConnection.textContent = "Reconnecting...";
  });
  room.on(RoomEvent.Reconnected, () => {
    hostConnection.textContent = "Connected";
  });
  room.on(RoomEvent.Disconnected, () => {
    if (!state.ending) hostConnection.textContent = "Disconnected";
  });
  room.on(RoomEvent.LocalTrackUnpublished, (publication) => {
    if (publication.source === Track.Source.ScreenShare) setHostSharing(false);
  });

  hostConnection.textContent = "Connecting...";
  await room.connect(livekitUrl(), tokenData.token);
  hostConnection.textContent = "Connected";
  updateCount();
}

function setHostSharing(sharing, track = null) {
  shareButton.classList.toggle("hidden", sharing);
  stopShareButton.classList.toggle("hidden", !sharing);
  hostPlaceholder.classList.toggle("hidden", sharing);
  hostVideo.classList.toggle("active", sharing);
  hostLiveBadge.textContent = sharing ? "Live" : "Not sharing";
  hostLiveBadge.classList.toggle("idle", !sharing);

  if (track) {
    track.attach(hostVideo);
  } else if (!sharing) {
    hostVideo.srcObject = null;
  }
}

async function startSharing() {
  setButtonBusy(shareButton, true, "Opening screen picker...");
  try {
    if (!state.room || state.room.state !== "connected") {
      await connectHost(state.hostSession);
    }
    await state.room.localParticipant.setScreenShareEnabled(
      true,
      {
        audio: false,
        resolution: { width: 1280, height: 720, frameRate: 15 },
      },
      {
        videoCodec: "vp8",
        screenShareEncoding: { maxBitrate: 1_000_000, maxFramerate: 15 },
      },
    );
    const { Track } = requireLiveKit();
    const publication = state.room.localParticipant.getTrackPublication(
      Track.Source.ScreenShare,
    );
    setHostSharing(true, publication?.videoTrack || null);
  } catch (error) {
    if (error.name !== "NotAllowedError") showToast(error.message);
  } finally {
    setButtonBusy(shareButton, false, "Opening screen picker...");
  }
}

async function stopSharing() {
  setButtonBusy(stopShareButton, true, "Stopping...");
  try {
    await state.room?.localParticipant.setScreenShareEnabled(false);
    setHostSharing(false);
  } catch (error) {
    showToast(error.message);
  } finally {
    setButtonBusy(stopShareButton, false, "Stopping...");
  }
}

async function createHostSession(event) {
  event.preventDefault();
  const submit = hostForm.querySelector("button[type='submit']");
  setButtonBusy(submit, true, "Creating...");
  try {
    const session = await api("/api/rooms", {
      method: "POST",
      body: { name: hostNameInput.value },
    });
    state.role = "host";
    state.hostSession = session;
    saveSession("classcast.host", session);
    hostPin.textContent = formatPin(session.pin);
    viewerLimit.textContent = String(session.maxViewers);
    showView("host");
    history.replaceState(null, "", "#host");
    await connectHost(session);
  } catch (error) {
    showToast(error.message);
  } finally {
    setButtonBusy(submit, false, "Creating...");
  }
}

function attachViewerTrack(track, publication) {
  const { Track } = requireLiveKit();
  if (publication.source !== Track.Source.ScreenShare) return;
  track.attach(viewerVideo);
  viewerVideo.classList.add("active");
  viewerPlaceholder.classList.add("hidden");
  viewerConnection.textContent = "Live";
}

function detachViewerTrack(track, publication) {
  const { Track } = requireLiveKit();
  if (publication.source !== Track.Source.ScreenShare) return;
  track.detach(viewerVideo);
  viewerVideo.srcObject = null;
  viewerVideo.classList.remove("active");
  viewerPlaceholder.classList.remove("hidden");
  viewerPlaceholderTitle.textContent = "Sharing has stopped";
  viewerPlaceholderText.textContent = "The screen will return automatically if the teacher shares again.";
  viewerConnection.textContent = "Waiting";
}

async function connectViewer(session) {
  const { RoomEvent } = requireLiveKit();
  if (state.room) await state.room.disconnect();
  const room = createRoom();
  state.room = room;

  room.on(RoomEvent.TrackSubscribed, attachViewerTrack);
  room.on(RoomEvent.TrackUnsubscribed, detachViewerTrack);
  room.on(RoomEvent.Reconnecting, () => {
    viewerConnection.textContent = "Reconnecting...";
  });
  room.on(RoomEvent.Reconnected, () => {
    viewerConnection.textContent = viewerVideo.srcObject ? "Live" : "Waiting";
  });
  room.on(RoomEvent.Disconnected, () => {
    if (!state.ending) {
      viewerConnection.textContent = "Disconnected";
      viewerPlaceholder.classList.remove("hidden");
      viewerPlaceholderTitle.textContent = "Connection ended";
      viewerPlaceholderText.textContent = "Return home and enter the PIN again to reconnect.";
    }
  });

  viewerConnection.textContent = "Connecting...";
  await room.connect(livekitUrl(), session.token);
  viewerConnection.textContent = "Waiting";
  startViewerHeartbeat();
}

async function joinViewerSession(event) {
  event.preventDefault();
  const submit = joinForm.querySelector("button[type='submit']");
  const pin = normalizePin(pinInput.value);
  if (pin.length !== 6) {
    showToast("Enter the six-digit PIN shown by your teacher.");
    return;
  }
  setButtonBusy(submit, true, "Joining...");
  try {
    const session = await api("/api/viewers/join", {
      method: "POST",
      body: { pin, name: studentNameInput.value },
    });
    state.role = "viewer";
    state.viewerSession = session;
    saveSession("classcast.viewer", session);
    viewerPin.textContent = formatPin(pin);
    showView("viewer");
    history.replaceState(null, "", `#join=${pin}`);
    await connectViewer(session);
  } catch (error) {
    showToast(error.message);
  } finally {
    setButtonBusy(submit, false, "Joining...");
  }
}

function startViewerHeartbeat() {
  clearInterval(state.heartbeatTimer);
  state.heartbeatTimer = setInterval(async () => {
    if (!state.viewerSession) return;
    try {
      await api("/api/viewers/heartbeat", {
        method: "POST",
        body: {
          pin: state.viewerSession.pin,
          viewerId: state.viewerSession.viewerId,
          viewerKey: state.viewerSession.viewerKey,
        },
      });
    } catch (error) {
      clearInterval(state.heartbeatTimer);
      showToast(error.message);
    }
  }, 15_000);
}

async function endHostSession() {
  if (!state.hostSession) return;
  const confirmed = window.confirm("End this session for everyone?");
  if (!confirmed) return;
  state.ending = true;
  try {
    await stopSharing();
    await api("/api/rooms/end", {
      method: "POST",
      body: {
        pin: state.hostSession.pin,
        hostKey: state.hostSession.hostKey,
      },
    });
  } catch (error) {
    showToast(error.message);
  } finally {
    await state.room?.disconnect();
    clearSession("classcast.host");
    state.hostSession = null;
    state.room = null;
    state.ending = false;
    history.replaceState(null, "", window.location.pathname);
    showView("landing");
  }
}

async function leaveViewer() {
  const session = state.viewerSession;
  if (!session) return;
  state.ending = true;
  clearInterval(state.heartbeatTimer);
  const body = {
    pin: session.pin,
    viewerId: session.viewerId,
    viewerKey: session.viewerKey,
  };
  await api("/api/viewers/leave", { method: "POST", body }).catch(() => {});
  await state.room?.disconnect();
  clearSession("classcast.viewer");
  state.viewerSession = null;
  state.room = null;
  state.ending = false;
  history.replaceState(null, "", window.location.pathname);
  showView("landing");
}

async function resumeFromHash() {
  const hash = window.location.hash;
  if (hash === "#host") {
    const session = loadSession("classcast.host");
    if (!session) {
      history.replaceState(null, "", window.location.pathname);
      return;
    }
    state.role = "host";
    state.hostSession = session;
    hostPin.textContent = formatPin(session.pin);
    viewerLimit.textContent = String(session.maxViewers || state.config.maxViewers);
    showView("host");
    try {
      await connectHost(session);
    } catch (error) {
      clearSession("classcast.host");
      showToast(error.message);
      showView("landing");
    }
    return;
  }
  if (hash.startsWith("#join=")) {
    const pin = normalizePin(hash.slice(6));
    const session = loadSession("classcast.viewer");
    pinInput.value = formatPin(pin);
    if (
      session
      && session.pin === pin
      && new Date(session.expiresAt).getTime() > Date.now()
    ) {
      state.role = "viewer";
      state.viewerSession = session;
      viewerPin.textContent = formatPin(pin);
      showView("viewer");
      try {
        await connectViewer(session);
      } catch (error) {
        showToast(error.message);
      }
    }
  }
}

pinInput.addEventListener("input", () => {
  pinInput.value = formatPin(pinInput.value);
});
hostForm.addEventListener("submit", createHostSession);
joinForm.addEventListener("submit", joinViewerSession);
shareButton.addEventListener("click", startSharing);
stopShareButton.addEventListener("click", stopSharing);
endSessionButton.addEventListener("click", endHostSession);
leaveButton.addEventListener("click", leaveViewer);
copyPinButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(state.hostSession.pin);
  showToast("PIN copied.", "success");
});

async function boot() {
  state.config = await api("/api/config");
  viewerLimit.textContent = String(state.config.maxViewers);
  await resumeFromHash();
}

boot().catch((error) => showToast(error.message));
