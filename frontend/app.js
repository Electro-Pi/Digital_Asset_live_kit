const connectBtn = document.getElementById('connectBtn');
const statusText = document.getElementById('statusText');
const visualizer = document.getElementById('visualizer');

let room = null;

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
                document.body.appendChild(audioEl);
                visualizer.classList.add('active'); // Start moving visualizer bars
            }
        });

        room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
            track.detach();
            visualizer.classList.remove('active');
        });

        room.on(LivekitClient.RoomEvent.Disconnected, () => {
            handleDisconnectUI();
        });

        // 3.5 Handle incoming text responses from Agent over DataChannel
        room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
            const textMsg = new TextDecoder("utf-8").decode(payload);
            const chatContainer = document.getElementById('chatContainer');
            const msgDiv = document.createElement('div');
            msgDiv.className = 'chat-message agent-msg';
            msgDiv.dir = 'auto'; // Support Arabic RTL formatting properly
            msgDiv.innerText = textMsg;
            chatContainer.appendChild(msgDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight; // Auto-scroll to bottom
        });

        // 4. Connect to Livekit
        await room.connect(data.url, data.token);
        
        // 5. Connect and enable local mic (so agent can hear you)
        await room.localParticipant.setMicrophoneEnabled(true);
        
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
    visualizer.classList.remove('active');
    connectBtn.innerText = "Start Conversation";
    connectBtn.classList.remove('danger');
    connectBtn.classList.add('primary');
    connectBtn.disabled = false;
}

connectBtn.addEventListener('click', () => {
    if (room && room.state !== 'disconnected') {
        handleDisconnectUI();
    } else {
        connectToAgent();
    }
});
