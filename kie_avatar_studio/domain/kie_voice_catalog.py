"""Catálogo built-in de voces TTS expuesto por Kie.ai.

Kie no expone un endpoint de listado de voces; las 67 entradas curadas vienen
embebidas en el OpenAPI spec del endpoint
`elevenlabs/text-to-speech-multilingual-v2` (también las usa
`text-to-speech-turbo-2-5`). Fuente:
https://docs.kie.ai/market/elevenlabs/text-to-speech-multilingual-v2

Mantener este archivo sincronizado a mano cuando Kie publique cambios al spec.
La constante es `Final` para que cualquier intento de mutación falle en `mypy`
(CR-3.4 sin código mutable global).

Preview de cualquier voz:
    https://static.aiquickdraw.com/elevenlabs/voice/<voice_id>.mp3

El campo `voice` del endpoint TTS también acepta voice_ids fuera de este catálogo
(p. ej. voces clonadas de una cuenta ElevenLabs Pro). Por eso la validación en
`policies.validate_voice_id` permite IDs custom: el catálogo es informativo
para la UI, no una restricción dura del API.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field


class KieVoice(BaseModel):
    """Voz TTS expuesta por Kie en su catálogo curado.

    `voice_id` es el identificador de ElevenLabs que se manda al endpoint.
    `label` y `description` vienen del spec y son para mostrar en la UI.
    `description` puede ser vacío para voces que el spec no describe.
    """

    voice_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""

    @property
    def preview_url(self) -> str:
        """URL del preview MP3 (servido público por Kie/aiquickdraw)."""
        return f"https://static.aiquickdraw.com/elevenlabs/voice/{self.voice_id}.mp3"

    @property
    def display_name(self) -> str:
        """Etiqueta legible para mostrar en selects ("Label — Description")."""
        if self.description:
            return f"{self.label} — {self.description}"
        return self.label


# Catálogo built-in: 67 voces parseadas del OpenAPI spec.
# Orden conservado tal como aparecen en el `enum` del campo `voice`.
BUILTIN_VOICES: Final[tuple[KieVoice, ...]] = (
    KieVoice(
        voice_id="EkK5I93UQWFDigLMpZcX", label="James", description="Husky, Engaging and Bold"
    ),
    KieVoice(
        voice_id="Z3R5wn05IrDiVCyEkUrK", label="Arabella", description="Mysterious and Emotive"
    ),
    KieVoice(
        voice_id="NNl6r8mD7vthiJatiJt1", label="Bradford", description="Expressive and Articulate"
    ),
    KieVoice(
        voice_id="YOq2y2Up4RgXP2HyXjE5",
        label="Xavier",
        description="Dominating, Metallic Announcer",
    ),
    KieVoice(
        voice_id="B8gJV1IhpuegLxdpXFOE", label="Kuon", description="Cheerful, Clear and Steady"
    ),
    KieVoice(voice_id="2zRM7PkgwBPiau2jvVXc", label="Monika Sogam", description="Deep and Natural"),
    KieVoice(
        voice_id="1SM7GgM6IMuvQlz2BwM3", label="Mark", description="Casual, Relaxed and Light"
    ),
    KieVoice(
        voice_id="5l5f8iK3YPeGga21rQIX", label="Adeline", description="Feminine and Conversational"
    ),
    KieVoice(voice_id="scOwDtmlUjD3prqpp97I", label="Sam", description="Support Agent"),
    KieVoice(
        voice_id="NOpBlnGInO9m6vDvFkFC", label="Spuds Oxley", description="Wise and Approachable"
    ),
    KieVoice(
        voice_id="BZgkqPqms7Kj9ulSkVzn", label="Eve", description="Authentic, Energetic and Happy"
    ),
    KieVoice(voice_id="wo6udizrrtpIxWGp2qJk", label="Northern Terry"),
    KieVoice(voice_id="gU0LNdkMOQCOrPrwtbee", label="British Football Announcer"),
    KieVoice(
        voice_id="DGzg6RaUqxGRTHSBjfgF", label="Brock", description="Commanding and Loud Sergeant"
    ),
    KieVoice(voice_id="x70vRnQBMBu4FAYhjJbO", label="Nathan", description="Virtual Radio Host"),
    KieVoice(
        voice_id="Sm1seazb4gs7RSlUVw7c",
        label="Anika",
        description="Animated, Friendly and Engaging",
    ),
    KieVoice(voice_id="P1bg08DkjqiVEzOn76yG", label="Viraj", description="Rich and Soft"),
    KieVoice(
        voice_id="qDuRKMlYmrm8trt5QyBn", label="Taksh", description="Calm, Serious and Smooth"
    ),
    KieVoice(
        voice_id="qXpMhyvQqiRxWQs4qSSB", label="Horatius", description="Energetic Character Voice"
    ),
    KieVoice(
        voice_id="TX3LPaxmHKxFdv7VOQHJ", label="Liam", description="Energetic, Social Media Creator"
    ),
    KieVoice(voice_id="N2lVS1w4EtoT3dr4eOWO", label="Callum", description="Husky Trickster"),
    KieVoice(
        voice_id="FGY2WhTYpPnrIDTdsKH5", label="Laura", description="Enthusiast, Quirky Attitude"
    ),
    KieVoice(
        voice_id="kPzsL2i3teMYv0FxEYQ6",
        label="Brittney",
        description="Social Media Voice - Fun, Youthful & Informative",
    ),
    KieVoice(voice_id="UgBBYS2sOqTuMpoF3BR0", label="Mark", description="Natural Conversations"),
    KieVoice(
        voice_id="hpp4J3VqNfWAUOO0d1Us", label="Bella", description="Professional, Bright, Warm"
    ),
    KieVoice(
        voice_id="nPczCjzI2devNBz1zQrb", label="Brian", description="Deep, Resonant and Comforting"
    ),
    KieVoice(
        voice_id="uYXf8XasLslADfZ2MB4u", label="Hope", description="Bubbly, Gossipy and Girly"
    ),
    KieVoice(
        voice_id="gs0tAILXbY5DNrJrsM6F", label="Jeff", description="Classy, Resonating and Strong"
    ),
    KieVoice(
        voice_id="DTKMou8ccj1ZaWGBiotd", label="Jamahal", description="Young, Vibrant, and Natural"
    ),
    KieVoice(
        voice_id="vBKc2FfBKJfcZNyEt1n6", label="Finn", description="Youthful, Eager and Energetic"
    ),
    KieVoice(voice_id="DYkrAHD8iwork3YSUBbs", label="Tom", description="Conversations & Books"),
    KieVoice(
        voice_id="56AoDkrOh6qfVPDXZ7Pt", label="Cassidy", description="Crisp, Direct and Clear"
    ),
    KieVoice(
        voice_id="eR40ATw9ArzDf9h3v7t7",
        label="Addison 2.0",
        description="Australian Audiobook & Podcast",
    ),
    KieVoice(
        voice_id="g6xIsTj2HwM6VR4iXFCw",
        label="Jessica Anne Bogart",
        description="Chatty and Friendly",
    ),
    KieVoice(voice_id="lcMyyd2HUfFzxdCaC4Ta", label="Lucy", description="Fresh & Casual"),
    KieVoice(voice_id="6aDn1KB0hjpdcocrUkmq", label="Tiffany", description="Natural and Welcoming"),
    KieVoice(
        voice_id="Sq93GQT4X1lKDXsQcixO",
        label="Felix",
        description="Warm, Positive & Contemporary RP",
    ),
    KieVoice(
        voice_id="flHkNRp1BlvT73UL6gyz", label="Jessica Anne Bogart", description="Eloquent Villain"
    ),
    KieVoice(
        voice_id="9yzdeviXkFddZ4Oz8Mok", label="Lutz", description="Chuckling, Giggly and Cheerful"
    ),
    KieVoice(voice_id="pPdl9cQBQq4p6mRkZy2Z", label="Emma", description="Adorable and Upbeat"),
    KieVoice(
        voice_id="zYcjlYFOd3taleS0gkk3", label="Edward", description="Loud, Confident and Cocky"
    ),
    KieVoice(
        voice_id="nzeAacJi50IvxcyDnMXa", label="Marshal", description="Friendly, Funny Professor"
    ),
    KieVoice(
        voice_id="ruirxsoakN0GWmGNIo04", label="John Morgan", description="Gritty, Rugged Cowboy"
    ),
    KieVoice(voice_id="TC0Zp7WVFzhA8zpTlRqV", label="Aria", description="Sultry Villain"),
    KieVoice(
        voice_id="ljo9gAlSqKOvF6D8sOsX", label="Viking Bjorn", description="Epic Medieval Raider"
    ),
    KieVoice(voice_id="PPzYpIqttlTYA83688JI", label="Pirate Marshal"),
    KieVoice(
        voice_id="8JVbfL6oEdmuxKn5DK2C", label="Johnny Kid", description="Serious and Calm Narrator"
    ),
    KieVoice(
        voice_id="iCrDUkL56s3C8sCRl7wb",
        label="Hope",
        description="Poetic, Romantic and Captivating",
    ),
    KieVoice(
        voice_id="wJqPPQ618aTW29mptyoc",
        label="Ana Rita",
        description="Smooth, Expressive and Bright",
    ),
    KieVoice(voice_id="EiNlNiXeDU1pqqOPrYMO", label="John Doe", description="Deep"),
    KieVoice(
        voice_id="4YYIPFl9wE5c4L2eu2Gb",
        label="Burt Reynolds™",
        description="Deep, Smooth and Clear",
    ),
    KieVoice(
        voice_id="6F5Zhi321D3Oq7v1oNT4", label="Hank", description="Deep and Engaging Narrator"
    ),
    KieVoice(voice_id="YXpFCvM1S3JbWEJhoskW", label="Wyatt", description="Wise Rustic Cowboy"),
    KieVoice(
        voice_id="LG95yZDEHg6fCZdQjLqj", label="Phil", description="Explosive, Passionate Announcer"
    ),
    KieVoice(
        voice_id="CeNX9CMwmxDxUF5Q2Inm", label="Johnny Dynamite", description="Vintage Radio DJ"
    ),
    KieVoice(
        voice_id="aD6riP1btT197c6dACmy", label="Rachel M", description="Pro British Radio Presenter"
    ),
    KieVoice(voice_id="mtrellq69YZsNwzUSyXh", label="Rex Thunder", description="Deep N Tough"),
    KieVoice(voice_id="dHd5gvgSOzSfduK4CvEg", label="Ed", description="Late Night Announcer"),
    KieVoice(
        voice_id="eVItLK1UvXctxuaRV2Oq",
        label="Jean",
        description="Alluring and Playful Femme Fatale",
    ),
    KieVoice(
        voice_id="esy0r39YPLQjOczyOib8", label="Britney", description="Calm and Calculative Villain"
    ),
    KieVoice(voice_id="Tsns2HvNFKfGiNjllgqo", label="Sven", description="Emotional and Nice"),
    KieVoice(voice_id="1U02n4nD6AdIZ9CjF053", label="Viraj", description="Smooth and Gentle"),
    KieVoice(
        voice_id="AeRdCCKzvd23BpJoofzx", label="Nathaniel", description="Engaging, British and Calm"
    ),
    KieVoice(voice_id="LruHrtVF6PSyGItzMNHS", label="Benjamin", description="Deep, Warm, Calming"),
    KieVoice(
        voice_id="1wGbFxmAM3Fgw63G1zZJ",
        label="Allison",
        description="Calm, Soothing and Meditative",
    ),
    KieVoice(
        voice_id="hqfrgApggtO1785R4Fsn", label="Theodore HQ", description="Serene and Grounded"
    ),
    KieVoice(voice_id="MJ0RnG71ty4LH3dvNfSd", label="Leon", description="Soothing and Grounded"),
)


_BY_ID: Final[dict[str, KieVoice]] = {voice.voice_id: voice for voice in BUILTIN_VOICES}


def get_builtin_voice(voice_id: str) -> KieVoice | None:
    """Devuelve la voz built-in con ese `voice_id`, o `None` si no está catalogada.

    Útil para resolver un `voice_id` (persistido en `GeneratedAudio`) a su
    label legible en la UI sin recorrer la tupla cada vez.
    """
    return _BY_ID.get(voice_id)


def is_builtin_voice(voice_id: str) -> bool:
    """`True` si `voice_id` pertenece al catálogo curado de Kie."""
    return voice_id in _BY_ID
