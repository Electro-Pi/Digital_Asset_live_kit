const connectBtn = document.getElementById('connectBtn');
const muteBtn = document.getElementById('muteBtn');
const statusText = document.getElementById('statusText');
const localViz = document.getElementById('localViz');
const remoteViz = document.getElementById('remoteViz');
const localPill = document.getElementById('localPill');
const remotePill = document.getElementById('remotePill');

let room = null;
let remoteAudioEls = [];
let micEnabled = false;

function setVizActive(el, active) {
    if (!el) return;
    el.classList.toggle('active', !!active);
}

async function requestMicrophone() {
    try {
        await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch(e) {
        throw new Error("Microphone access gets denied. We need it to interact with the voice agent!");
    }
}

async function connectToAgent() {
    try {
        // Prepare UI state
        statusText.innerText = "Connecting...";
        connectBtn.disabled = true;
        connectBtn.innerText = "Please Wait...";
        muteBtn.disabled = true;
        
        // 1. Authorize mic manually first to ensure playback policy matches
        await requestMicrophone();
        
        // 2. Fetch connection details from Python FastAPI server
        const response = await fetch('/token');
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Failed to fetch token. Ensure .env.local is valid.");
        }
        
        // 3. Setup LiveKit Room event handlers BEFORE connecting
        room = new LivekitClient.Room({
            adaptiveStream: true,
            dynacast: true,
            audioCaptureDefaults: {
                autoGainControl: true,
                echoCancellation: true,
                noiseSuppression: true,
            }
        });
        
        room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
            // Agent's track arrived!
            if (track.kind === LivekitClient.Track.Kind.Audio) {
                const audioEl = track.attach();
                audioEl.autoplay = true;
                audioEl.playsInline = true;
                audioEl.dataset.lkParticipant = participant?.identity || '';
                document.body.appendChild(audioEl);
                remoteAudioEls.push(audioEl);

                // user gesture already happened (button click), so this should succeed
                Promise.resolve(audioEl.play()).catch(() => {});

                setVizActive(remoteViz, true);
                remotePill.innerText = "speaking";
            }
        });

        room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
            track.detach();
            setVizActive(remoteViz, false);
            remotePill.innerText = "silent";
        });

        room.on(LivekitClient.RoomEvent.Disconnected, () => {
            handleDisconnectUI();
        });

        // 4. Connect to Livekit
        await room.connect(data.url, data.token);
        
        // 5. Connect and enable local mic (so agent can hear you)
        await room.localParticipant.setMicrophoneEnabled(true);
        micEnabled = true;
        localPill.innerText = "mic on";
        setVizActive(localViz, true);
        muteBtn.disabled = false;
        muteBtn.innerText = "Mute Mic";
        
        // 6. Update UI
        statusText.innerText = "Agent is Listening";
        statusText.classList.add('connected');
        connectBtn.innerText = "End Conversation";
        connectBtn.classList.add('danger');
        connectBtn.classList.remove('primary');
        connectBtn.disabled = false;
        
    } catch (e) {
        console.error(e);
        statusText.innerText = "Connection Failed";
        alert("Error connecting: " + e.message);
        handleDisconnectUI();
    }
}

function handleDisconnectUI() {
    if (room) {
        room.disconnect();
        room = null;
    }
    statusText.innerText = "Disconnected";
    statusText.classList.remove('connected');
    micEnabled = false;
    localPill.innerText = "mic off";
    remotePill.innerText = "silent";
    setVizActive(localViz, false);
    setVizActive(remoteViz, false);

    // cleanup any attached remote audio elements
    for (const el of remoteAudioEls) {
        try { el.remove(); } catch (_) {}
    }
    remoteAudioEls = [];

    connectBtn.innerText = "Start Conversation";
    connectBtn.classList.remove('danger');
    connectBtn.classList.add('primary');
    connectBtn.disabled = false;
    muteBtn.disabled = true;
}

connectBtn.addEventListener('click', () => {
    if (room && room.state !== 'disconnected') {
        handleDisconnectUI();
    } else {
        connectToAgent();
    }
});

muteBtn.addEventListener('click', async () => {
    if (!room) return;
    micEnabled = !micEnabled;
    await room.localParticipant.setMicrophoneEnabled(micEnabled);
    localPill.innerText = micEnabled ? "mic on" : "mic off";
    setVizActive(localViz, micEnabled);
    muteBtn.innerText = micEnabled ? "Mute Mic" : "Unmute Mic";
});
