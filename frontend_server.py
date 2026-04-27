import os
import uuid
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from livekit.api import AccessToken, VideoGrants
from fastapi.responses import RedirectResponse

load_dotenv(".env.local")

app = FastAPI()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

@app.get("/token")
def get_token(room_name: str = None, participant_name: str = "web-user"):
    # Generate unique room on the fly to bypass livekit ghost dispatch lock
    room_name = room_name or f"my-room-{uuid.uuid4().hex[:8]}"

    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=500, detail="LiveKit credentials missing in .env.local.")
    if "your-project" in LIVEKIT_URL or "your_api_key" in LIVEKIT_API_KEY:
        raise HTTPException(status_code=500, detail="Please replace placeholder credentials in .env.local with real ones.")

    # Create an Access Token allowing the user to join, publish, subscribe, and use data channels.
    # Some deployments require these flags explicitly even though SDK defaults may work elsewhere.
    grant = VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    access_token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_name)
        .with_name(participant_name)
        .with_grants(grant)
    )

    return {
        "token": access_token.to_jwt(),
        "url": LIVEKIT_URL
    }

# Mount static files
os.makedirs("frontend", exist_ok=True)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    print("Starting frontend server at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8080)
