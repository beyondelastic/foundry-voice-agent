"""Create (or update) the lab bench voice agent in Microsoft Foundry.

Every run saves a new, immutable agent version. The agent endpoint is live as
soon as a version exists, so there is no separate deployment step.

    python create_agent.py
"""

import os
from datetime import timedelta
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    RealtimeAudioFormatsAudioPcm,
    RealtimeFunctionToolParameters,
    VoiceAgentAudioConfig,
    VoiceAgentAudioInputConfig,
    VoiceAgentAudioOutputConfig,
    VoiceAgentAzureSemanticVadTurnDetection,
    VoiceAgentDefinition,
    VoiceAgentEchoCancellation,
    VoiceAgentFunctionTool,
    VoiceAgentNoiseReduction,
    VoiceAgentTemplateGreetingConfig,
    VoiceModelType,
    VoiceOutputModality,
    VoiceType,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from tools import TOOL_SCHEMAS

load_dotenv()

endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
agent_name = os.environ.get("FOUNDRY_VOICE_AGENT_NAME", "lab-voice-assistant")
model = os.environ.get("FOUNDRY_VOICE_AGENT_MODEL", "gpt-realtime")
voice = os.environ.get("FOUNDRY_VOICE_AGENT_VOICE", "en-US-AvaNeural")

definition = VoiceAgentDefinition(
    model_type=VoiceModelType.MANAGED,
    model=model,
    instructions=(Path(__file__).parent / "instructions.md").read_text(encoding="utf-8"),
    greeting=VoiceAgentTemplateGreetingConfig(
        text="Hi, I'm Vita, your lab bench assistant. What can I set up for you?"
    ),
    audio=VoiceAgentAudioConfig(
        # Keep Vita from hearing herself (and the music) through open speakers.
        input=VoiceAgentAudioInputConfig(
            format=RealtimeAudioFormatsAudioPcm(rate=24000),
            # Live-reference echo cancellation: the browser sends stereo audio,
            # channel 0 = microphone, channel 1 = what the speakers are playing.
            echo_cancellation=VoiceAgentEchoCancellation(reference_source="client", channels=2),
            noise_reduction=VoiceAgentNoiseReduction(type="azure_deep_noise_suppression"),
            # Semantic turn detection with filler-word removal reduces false barge-ins.
            turn_detection=VoiceAgentAzureSemanticVadTurnDetection(
                threshold=0.6,
                speech_duration_ms=timedelta(milliseconds=200),
                silence_duration_ms=timedelta(milliseconds=500),
                remove_filler_words=True,
            ),
        ),
        output=VoiceAgentAudioOutputConfig(voice=voice, voice_type=VoiceType.AZURE_STANDARD),
    ),
    output_modalities=[VoiceOutputModality.AUDIO],
    # Function tools are executed by our app (app.py), not by the service.
    tools=[
        VoiceAgentFunctionTool(
            name=tool["name"],
            description=tool["description"],
            parameters=RealtimeFunctionToolParameters(tool["parameters"]),
        )
        for tool in TOOL_SCHEMAS
    ],
    store=True,
)

with (
    DefaultAzureCredential() as credential,
    AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project_client,
):
    created = project_client.agents.create_version(agent_name=agent_name, definition=definition)
    print(f"Voice agent '{agent_name}' saved as version {created.version} (model: {model}, voice: {voice})")
