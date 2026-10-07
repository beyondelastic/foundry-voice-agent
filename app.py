"""Lab bench voice assistant: a tiny FastAPI app between the browser and the Foundry voice agent.

    browser (mic + speaker + lab visual)  <-- /ws -->  app.py  <-- realtime -->  Foundry voice agent

- The browser streams microphone audio (PCM16, 24 kHz) to /ws. In stereo mode the
  second channel carries what the speakers play, so the service can cancel the echo.
- app.py forwards it to the voice agent and streams the spoken reply back.
- When the agent calls a tool, app.py runs it (tools.py) and pushes the new room state.

The Entra ID credential stays on the server, so no token ever reaches the browser.

    uvicorn app:app --reload
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    RealtimeConversationItemFunctionCallOutput,
    RealtimeServerEventConversationItemInputAudioTranscriptionCompleted,
    RealtimeServerEventError,
    RealtimeServerEventInputAudioBufferSpeechStarted,
    RealtimeServerEventResponseAudioDelta,
    RealtimeServerEventResponseAudioTranscriptDone,
    RealtimeServerEventResponseCreated,
    RealtimeServerEventResponseDone,
    RealtimeServerEventResponseFunctionCallArgumentsDone,
)
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import tools

load_dotenv()

ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
AGENT_NAME = os.environ.get("FOUNDRY_VOICE_AGENT_NAME", "lab-voice-assistant")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=ENDPOINT, credential=credential, allow_preview=True) as client,
    ):
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
async def room_state():
    return tools.STATE


@app.websocket("/ws")
async def voice_session(browser: WebSocket):
    await browser.accept()
    await browser.send_json({"type": "state", "state": tools.STATE})

    client: AIProjectClient = app.state.client
    try:
        # Tell the browser whether to send mono (mic only) or stereo (mic + speaker
        # reference for echo cancellation), matching the agent's current definition.
        await browser.send_json({"type": "config", "channels": await input_channels(client)})
        async with client.beta.voice_agents.realtime.connect(agent_name=AGENT_NAME) as agent:
            await browser.send_json({"type": "status", "text": "Connected, start talking."})
            mic = asyncio.create_task(browser_to_agent(browser, agent))
            replies = asyncio.create_task(agent_to_browser(agent, browser))
            # When either side hangs up, end the whole session.
            done, pending = await asyncio.wait({mic, replies}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except Exception as exc:  # surface connection problems in the UI
        print(f"Session error: {exc!r}")
        await safe_send(browser, {"type": "error", "text": str(exc)})
    await safe_send(browser, {"type": "status", "text": "Session ended."})
    try:
        await browser.close()
    except RuntimeError:
        pass  # the browser already closed the socket


async def input_channels(client: AIProjectClient) -> int:
    definition = (await client.agents.get(agent_name=AGENT_NAME)).versions.latest.definition
    echo = definition.audio and definition.audio.input and definition.audio.input.echo_cancellation
    return 2 if echo and echo.reference_source == "client" else 1


async def safe_send(browser: WebSocket, message: dict):
    try:
        await browser.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def browser_to_agent(browser: WebSocket, agent):
    """Forward raw PCM16 audio chunks (mono, or interleaved mic + reference) to the voice agent."""
    while True:
        audio = await browser.receive_bytes()
        await agent.input_audio_buffer.append(audio=audio)


async def agent_to_browser(agent, browser: WebSocket):
    """Relay agent events to the browser and execute function tool calls."""
    pending_outputs = []  # tool results waiting for the current response to finish
    response_active = False

    async for event in agent:
        if isinstance(event, RealtimeServerEventResponseAudioDelta):
            await browser.send_bytes(event.delta)  # PCM16, mono, 24 kHz

        elif isinstance(event, RealtimeServerEventResponseCreated):
            response_active = True
            await browser.send_json({"type": "response_started"})

        elif isinstance(event, RealtimeServerEventInputAudioBufferSpeechStarted):
            # Barge-in: the user started talking, so stop the agent's current reply.
            await browser.send_json({"type": "interrupt"})
            if response_active:
                await agent.response.cancel()

        elif isinstance(event, RealtimeServerEventConversationItemInputAudioTranscriptionCompleted):
            await browser.send_json({"type": "transcript", "role": "user", "text": event.transcript.strip()})

        elif isinstance(event, RealtimeServerEventResponseAudioTranscriptDone):
            await browser.send_json({"type": "transcript", "role": "agent", "text": event.transcript})

        elif isinstance(event, RealtimeServerEventResponseFunctionCallArgumentsDone):
            args = json.loads(event.arguments or "{}")
            result = tools.run_tool(event.name, args)
            print(f"Tool call: {event.name}({args}) -> {result}")
            pending_outputs.append((event.call_id, result))
            await browser.send_json({"type": "tool", "name": event.name, "args": args, "result": result})
            await browser.send_json({"type": "state", "state": tools.STATE})

        elif isinstance(event, RealtimeServerEventResponseDone):
            response_active = False
            # Send tool results only after the function-call response is done,
            # then ask the agent to continue so it can confirm the action.
            if pending_outputs:
                for call_id, result in pending_outputs:
                    await agent.conversation.item.create(
                        item=RealtimeConversationItemFunctionCallOutput(call_id=call_id, output=json.dumps(result))
                    )
                pending_outputs.clear()
                await agent.response.create()

        elif isinstance(event, RealtimeServerEventError):
            if event.error.code == "response_cancel_not_active":
                continue  # harmless: the reply had already finished when we cancelled
            print(f"Agent error: {event.error.message}")
            await browser.send_json({"type": "error", "text": event.error.message})
