import argparse, json, asyncio
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaPlayer
import av, cv2

pcs = set()

# Optional: use your webcam; for screen share, pass a desktop source via ffmpeg input instead.
def get_publisher_player():
    # Webcam (auto): use 1280x720/1920x1080 depending on camera
    # On Windows, try: MediaPlayer("video=YOUR_WEBCAM_NAME", format="dshow")
    return MediaPlayer(None)  # None uses default webcam + mic where available

class DownscaleTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, source_track, width, height):
        super().__init__()
        self.source = source_track
        self.w = width
        self.h = height

    async def recv(self):
        frame = await self.source.recv()
        img = frame.to_ndarray(format="bgr24")
        resized = cv2.resize(img, (self.w, self.h), interpolation=cv2.INTER_AREA)
        new_frame = av.VideoFrame.from_ndarray(resized, format="bgr24")
        new_frame.pts = frame.pts
        new_frame.time_base = frame.time_base
        return new_frame

# keep the latest publisher video track
state = {
    "publisher_pc": None,
    "publisher_video": None,
    "publisher_audio": None,
}

async def index(request):
    return web.Response(text="OK. Open /static/publisher.html to publish and /static/viewer.html?res=720 to watch.")

async def publisher_offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    def on_ice_state():
        if pc.iceConnectionState in ("failed", "closed", "disconnected"):
            pcs.discard(pc)
            if state["publisher_pc"] is pc:
                state["publisher_pc"] = None
                state["publisher_video"] = None
                state["publisher_audio"] = None

    # Accept incoming tracks from publisher (camera or screen)
    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            state["publisher_video"] = track
        elif track.kind == "audio":
            state["publisher_audio"] = track

    await pc.setRemoteDescription(offer)
    # Answer with no extra tracks: publisher sends tracks to server only.
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    state["publisher_pc"] = pc
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def viewer_offer(request):
    # Query ?res=1080|720|480
    res = request.query.get("res", "720")
    sizes = {"1080": (1920, 1080), "720": (1280, 720), "480": (854, 480)}
    w, h = sizes.get(res, (1280, 720))

    if state["publisher_video"] is None:
        return web.json_response({"error": "No publisher connected"}, status=409)

    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    def on_ice_state():
        if pc.iceConnectionState in ("failed", "closed", "disconnected"):
            pcs.discard(pc)

    # Downscale per viewer
    down_video = DownscaleTrack(state["publisher_video"], w, h)
    await pc.setRemoteDescription(offer)

    # Add downscaled video
    pc.addTrack(down_video)

    # Add audio if available
    if state["publisher_audio"]:
        pc.addTrack(state["publisher_audio"])

    # Optional: cap bitrate to fit resolution
    # (some browsers honor maxBitrate better from JS, but we keep server simple)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def on_shutdown(app):
    coros = [pc.close() for pc in pcs]
    if coros:
        await asyncio.gather(*coros)

def make_app(static_dir="static"):
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer/publisher", publisher_offer)
    app.router.add_post("/offer/viewer", viewer_offer)
    app.router.add_static("/static", static_dir)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    web.run_app(make_app(), host=args.host, port=args.port)
