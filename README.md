

# Riverwood AI Voice Agent

## Architecture Overview



```text
[ Admin / Scheduler ]
        | (POST /trigger/{user_id})
        v
+---------------------------------------------------+
|                   FastAPI Backend                 |
|                                                   |
|  1. Fetches User Context & Construction Update    |
|  2. Evaluates Call History (Returning vs New)     |
|  3. Dispatches initial outbound call via Twilio   |
+---------------------------------------------------+
        |                                 ^
        | (TwiML / REST)                  | (SpeechResult Webhook)
        v                                 |
+---------------------------------------------------+
|                 Twilio Voice API                  |
|  (Handles PSTN connectivity, Gather, recording)   |
+---------------------------------------------------+
        |
        v
[ Customer Phone ]
```

## Core Functionalities

### 1. Persistent Conversational Memory
The agent utilizes an integrated SQLite + SQLAlchemy database to persist user state across multiple distinct phone calls. 
* **Database Models:** Track User details, CRM flags (`site_visit_interest`), `ConstructionUpdate` records, and chronologically mapped `Interaction` chat histories.
* **Returning User Logic:** If the system initiates a call to a returning user, it acknowledges previous context rather than repeating standard scripted greetings.

### 2. Immediate "Busy Customer" Fallback
To handle real-world telephonic rejections efficiently without expending LLM tokens or incurring text-to-speech generation latency:
* The system scans real-time transcriptions for rejection markers ("busy", "call later", "wrong number", "cut the call").
* If detected, it entirely bypasses the LLM processing and immediately streams a pre-generated, zero-latency MP3 audio file.
* An automatic `<Hangup>` signal is fired back to Twilio to cleanly end the call within milliseconds.

### 3. Real-Time Intent Extraction & CRM Updating
As the user speaks, the system concurrently analyzes the transcription for context intent:
* If an agreement to a site visit is detected, the `site_visit_interest` boolean is permanently committed to the database.
* A pre-cached confirmation message is dispatched with zero generation delay before gracefully terminating the call.

### 4. Dynamic Knowledge Base Injection
The LLM (Google Gemini 2.5 Flash) operates smoothly by dynamically reconstructing a highly optimized context window before every single interaction:
* Injects the system prompt to enforce persona consistency.
* Appends the latest construction phase, precise percentage completion, and available visiting hours.
* Appends the last 10 interactions of historical conversation to maintain timeline continuity.

## System Call Flow

```text
1. OUTBOUND TRIGGER
   Backend -> Initiates Call via Twilio API -> Plays Greeting (gTTS)
   
2. USER SPEECH LOOP
   Twilio <Gather> -> Captures User Speech -> Sends to /api/process Webhook
   
3. FASTAPI PROCESSING PIPELINE
   |-- Check User Intent
   |   |-- IF "Busy / Not Interested": Stream Canned Audio (0ms) -> HANGUP
   |   |-- IF "Agrees to Visit": Update DB -> Stream Canned Audio (0ms) -> HANGUP
   |   |
   |-- IF Standard Conversation:
       |-- Retrieve prior context limits from persistence layer
       |-- Emit prompt payload to Gemini REST API
       |-- Stream synthetic output through gTTS
       |-- Return formatted TwiML <Play> payload
       
4. CONTINUATION
   Twilio plays generated audio -> Loops back to <Gather> unless termination flagged.
```

## Tech Stack

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend Framework** | FastAPI (Python) | API routing, webhook processing, state management |
| **Telephony & STT** | Twilio | PSTN network interface and Speech-to-Text inference |
| **LLM Engine** | Google Gemini 2.5 Flash | Conversational generation (asynchronous HTTP integration) |
| **Text-to-Speech** | gTTS (Google TTS) | Native low-overhead audio file output |
| **Database / Memory** | SQLite + SQLAlchemy | Strict persistence for profiles, states, and history logs |

## Project Structure

```text
riverwood-ai-challenge/
|-- agent.py             # Core prompt assembly, DB integration, and intent mapping
|-- db.py                # Database configurations and declarative schema design
|-- llm_gemma.py         # HTTPX integration adapter for Google Gemini API
|-- main.py              # Primary application routines and webhook resolution
|-- telephony.py         # Outbound pipeline integration
|-- tts.py               # Text-to-Speech serialization and latent caching map
|-- seed_db.py           # Populates environment base for demonstration mock-data
|-- trigger_call.py      # Entry-point for test environment orchestration
`-- requirements.txt     # Environment dependencies
```

