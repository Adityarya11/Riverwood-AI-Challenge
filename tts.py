import os
from gtts import gTTS
import hashlib

TTS_OUTPUT_DIR = os.getenv("TTS_OUTPUT_DIR", "./audio")
os.makedirs(TTS_OUTPUT_DIR, exist_ok=True)

CANNED = {
    "visit_confirm_en": "Great! I have noted that you are interested in a site visit. Our team will contact you shortly to schedule it. Goodbye!",
    "visit_confirm_hi": "Bahut achha! Maine aapka site visit note kar liya hai. Hamari team jald aapse sampark karegi. Namaste!",
    "busy_fallback_en": "I understand you are busy. I will call you back later. Have a wonderful day!",
    "busy_fallback_hi": "Maaf kijiye, main aapko baad mein call karungi. Aapka din shubh ho!"
}

def text_to_speech(text: str, lang: str = "en", filename_hint: str = "reply") -> str:
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    fname = f"{filename_hint}_{key}.mp3"
    out_path = os.path.join(TTS_OUTPUT_DIR, fname)

    if os.path.exists(out_path):
        return out_path

    tts = gTTS(text=text, lang=lang)
    tts.save(out_path)
    return out_path

def get_or_create_canned(key: str, lang: str = "en") -> str:
    text = CANNED.get(key, "")
    return text_to_speech(text, lang=lang, filename_hint=f"canned_{key}")