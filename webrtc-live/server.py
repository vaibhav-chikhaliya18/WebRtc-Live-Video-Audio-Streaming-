# server.py
import asyncio
import logging
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaRelay

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-demo")

pcs = set()
relay = MediaRelay()          # to allow relaying a single publisher to many viewers
broadcaster_tracks = {}       # store original tracks from the broadcaster

async def offer(request):
    """
    Handle POST /offer
    JSON body: { sdp: "...", type: "offer", role: "broadcaster" | "viewer" }
    """
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    role = params.get("role", "viewer")

    pc = RTCPeerConnection()
    pcs.add(pc)
    logger.info("New connection (role=%s). Current pcs=%d", role, len(pcs))

    @pc.on("iceconnectionstatechange")
    async def on_ice():
        logger.info("ICE %s", pc.iceConnectionState)
        if pc.iceConnectionState in ("failed", "disconnected", "closed"):
            await pc.close()
            pcs.discard(pc)

    if role == "broadcaster":
        @pc.on("track")
        def on_track(track):
            # store the publisher's original track; later viewers can subscribe via MediaRelay
            logger.info("Broadcaster track received: %s", track.kind)
            broadcaster_tracks[track.kind] = track

            @track.on("ended")
            def on_ended():
                logger.info("Broadcaster track ended: %s", track.kind)
                broadcaster_tracks.pop(track.kind, None)

    else:  # viewer
        # if a broadcaster has already published tracks, add relayed tracks to this PC
        for kind, original in broadcaster_tracks.items():
            logger.info("Adding relayed %s track to viewer", kind)
            pc.addTrack(relay.subscribe(original))

        # Note: this simple demo requires broadcaster to already be streaming.
        # If viewer connects before broadcaster, they will receive no media.

    # standard SDP exchange
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
    )

async def on_shutdown(app):
    logger.info("Shutting down, closing peer connections")
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()

app = web.Application()
app.router.add_post("/offer", offer)
# serve static files from ./static (index.html)
app.router.add_static("/", path="./static", show_index=True)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=8080)
