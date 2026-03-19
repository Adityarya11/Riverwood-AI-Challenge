import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.voice_response import VoiceResponse, Gather
from agent import trigger_outbound_call, process_user_speech
from db import Base, engine
from dotenv import load_dotenv
from vapi_handler import vapi_router

load_dotenv()
Base.metadata.create_all(bind=engine)

os.makedirs(os.getenv("TTS_OUTPUT_DIR", "./audio"), exist_ok=True)

app = FastAPI(title="Riverwood Voice Agent Prototype")
app.include_router(vapi_router)
app.mount("/static", StaticFiles(directory=os.getenv("TTS_OUTPUT_DIR", "./audio")), name="static")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/trigger/{user_id}")
async def trigger(user_id: str):
    try:
        result = await trigger_outbound_call(user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result

@app.post("/api/process")
async def twilio_process_speech(request: Request, user_id: str):
    """Processes recursive execution cycles bound to Twilio webhooks."""
    form_data = await request.form()
    user_speech = form_data.get('SpeechResult', '')
    
    NGROK_URL = os.getenv("NGROK_URL", os.getenv("BASE_URL"))
    response = VoiceResponse()
    
    # Empty speech array bypasses pipeline processing
    if not user_speech:
        gather = Gather(input='speech', action=f'{NGROK_URL}/api/process?user_id={user_id}', method='POST', timeout=10)
        
        gather.say("I didn't quite catch that. Could you repeat?", voice='alice')
        response.append(gather)
        
        return Response(content=str(response), media_type="application/xml")
        
    # Asynchronous pipeline deployment
    audio_path, assistant_text, should_hangup = await process_user_speech(user_id, user_speech)
    audio_url = f"{NGROK_URL}/static/{os.path.basename(audio_path)}"
    
    # Internal validation for application-side disconnection
    if should_hangup or "goodbye" in assistant_text.lower() or "bye" in assistant_text.lower() or "namaste" in assistant_text.lower():
        response.play(audio_url)
        response.hangup()
    else:
        # Standard continuation loop
        gather = Gather(input='speech', action=f'{NGROK_URL}/api/process?user_id={user_id}', method='POST', speechTimeout='auto', timeout=5)
        gather.play(audio_url)
        response.append(gather)
        
    return Response(content=str(response), media_type="application/xml")