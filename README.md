# Riverwood AI Voice Agent

An AI-powered outbound voice agent for **Riverwood Projects LLP** that delivers construction progress updates to customers and captures site visit intentions — all via natural phone conversations in Hindi and English.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    VAPI Orchestrator                    │
│  (WebRTC · SIP · Deepgram STT · ElevenLabs TTS · LLM)   │
└──────────┬──────────────────────────────────────┬───────┘
           │  Outbound Call API                   │  Webhook Events
           │  (POST /call)                        │  (tool-calls, status,  report)
           ▼                                      ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI Backend (Your Server)              │
│                                                         │
│  POST /api/call        → Trigger outbound call          │
│  POST /api/bulk-call   → Trigger batch calls            │
│  POST /api/webhook     → Handle VAPI events & tools     │
│  GET  /api/users       → Customer database              │
│  GET  /api/visits      → Visit intention records        │
│  GET  /api/call-history→ Past call summaries            │
└──────────┬──────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐
│   Memory Store       │
│  (In-Memory Dict)    │
│  • User profiles     │
│  • Construction data │
│  • Visit intentions  │
│  • Call history      │
└──────────────────────┘
```

### How It Works

1. **Call Trigger**: The backend sends a `POST /api/call` request. This calls VAPI's REST API with an inline assistant configuration — the customer's name, project details, and construction update are all injected into the system prompt dynamically.

2. **Voice Conversation**: VAPI handles the real-time audio pipeline:
   - **STT** (Deepgram) transcribes the customer's speech.
   - **LLM** (GPT-4o) generates contextual responses using the injected system prompt.
   - **TTS** (ElevenLabs) converts text to natural, warm speech.

3. **Tool Calling**: When the LLM determines the customer has answered the visit question, it triggers a function call. VAPI sends this as an HTTP POST to the FastAPI webhook, which records the data and returns confirmation to the LLM.

4. **Memory / Context Retention**: After each call ends, VAPI sends an `end-of-call-report` with a transcript and summary. The backend stores this so future calls can reference previous interactions — enabling conversational continuity across calls.

---

## Tech Stack

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend / API** | Python, FastAPI | Webhook processing, state management, call orchestration |
| **Voice Orchestration** | VAPI | Manages WebRTC, SIP, and real-time audio streaming |
| **LLM Engine** | OpenAI GPT-4o | Conversational logic and context understanding |
| **Voice Synthesis (TTS)** | ElevenLabs (via VAPI) | Ultra-realistic speech in English and Hindi |
| **Speech-to-Text (STT)** | Deepgram (via VAPI) | Real-time speech transcription |
| **Memory / Storage** | In-Memory Dict | User profiles, call history, visit intention records |

---

## Project Structure

```
riverwood-voice-agent/
├── app/
│   ├── __init__.py       # Package marker
│   ├── main.py           # FastAPI app + webhook endpoints
│   ├── models.py         # Pydantic models for VAPI payloads
│   ├── memory.py         # Mock database + state management
│   └── services.py       # VAPI REST API integration
├── .env                  # Secret keys (VAPI, ngrok URL, etc.)
├── .gitignore            # Standard Python gitignore
├── requirements.txt      # Python dependencies
└── README.md             # This file — architecture + technical note
```

---

<!-- ## Setup & Run

### Prerequisites

- Python 3.10+
- VAPI account with API key and a phone number
- ngrok (for local development — exposes your localhost to VAPI)

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

Edit `.env` with your credentials:

```env
VAPI_API_KEY=your_vapi_api_key
VAPI_PHONE_NUMBER_ID=your_phone_number_id
SERVER_URL=https://xxxx.ngrok.io
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
```

### Run

```bash
# Terminal 1 — Start the FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Expose via ngrok
ngrok http 8000
```

Copy the ngrok HTTPS URL and set it as `SERVER_URL` in `.env`.

### Trigger a Call

```bash
curl -X POST http://localhost:8000/api/call \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_001"}'
``` -->

### Example Conversation Flow

1. **Aditya** (AI): *"Hello! Kya main Rahul Sharma ji se baat kar rahi hoon? Namaste! Main Aditya hoon, Riverwood Projects ki taraf se call kar rahi hoon. Aap kaise hain?"*
2. **Customer**: *"Haan, Rahul bol raha hoon. Theek hoon."*
3. **Aditya**: *"Bahut accha! Main aapko aapke 3BHK unit ka construction update dene ke liye call kar rahi hoon. Tower A mein abhi Phase 3 chal raha hai — interior finishing. 72% kaam complete ho chuka hai, aur flooring aur painting agle hafte se shuru ho rahi hai."*
4. **Customer**: *"Wah, ye toh acchi khabar hai!"*
5. **Aditya**: *"Ji bilkul! Kya aap is weekend site visit karna chahenge? Saturday-Sunday 10 se 5 baje tak visit available hai."*
6. **Customer**: *"Haan, Saturday ko aa sakta hoon."*
7. **Aditya** → *calls `record_site_visit` tool* → *"Bahut badhiya! Maine Saturday ke liye aapki visit note kar li hai. Humari site team aapka intezaar karegi. Dhanyavaad Rahul ji, aapka din shubh ho!"*

---

## API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/call` | Trigger a single outbound call |
| `POST` | `/api/bulk-call` | Trigger batch outbound calls |
| `POST` | `/api/webhook` | VAPI webhook handler (tool-calls, status, reports) |
| `GET` | `/api/users` | List all customers |
| `GET` | `/api/users/{id}` | Get customer details + call history |
| `GET` | `/api/visits` | List all recorded visit intentions |
| `GET` | `/api/call-history/{id}` | Get past call summaries for a user |
| `GET` | `/api/callbacks` | List all scheduled callbacks |
| `GET` | `/health` | Health check |

---

## Scaling to 1000 Calls/Morning

### Infrastructure Design

```
┌──────────┐    ┌──────────────┐    ┌────────────────┐    ┌──────────┐
│  Cron /  │───▶│  Message     │───▶│  Worker Pool   │───▶│  VAPI    │
│Scheduler │    │  Queue (SQS) │    │  (K8s / ECS)   │    │  API     │
└──────────┘    └──────────────┘    └────────────────┘    └──────────┘
                                           │
                                    ┌──────┴──────┐
                                    │ PostgreSQL  │
                                    │ (Call State)│
                                    └─────────────┘
```

1. **Scheduler** (AWS EventBridge / cron): Triggers the batch job at the configured time each morning.
2. **Message Queue** (AWS SQS / RabbitMQ): Receives 1000 call tasks. Provides durability, retry logic, and backpressure.
3. **Worker Pool** (Kubernetes / AWS ECS): 5–10 worker instances pull tasks from the queue and dispatch calls via VAPI. Auto-scales based on queue depth.
4. **Rate Limiter**: Workers respect VAPI's API rate limits (typically 10–50 concurrent calls). A token bucket algorithm controls dispatch rate.
5. **State Database** (PostgreSQL): Tracks call status (queued → in-progress → completed/failed), enables retries for failed calls, and stores outcomes.
6. **Monitoring** (CloudWatch / Prometheus): Dashboards track call success rate, average latency, and error rates. Alerts fire on failure spikes.

### Key Optimizations

- **Staggered Dispatch**: Spread calls over a 1–2 hour window to avoid thundering herd.
- **Concurrency Control**: Max 20–30 simultaneous calls balancing speed vs. quality.
- **Smart Retries**: Failed calls re-queued with exponential backoff (max 3 attempts).
- **Priority Queues**: High-value customers called first.

### Estimated Cost per 1000 Calls

| Item | Unit Cost | Quantity | Total |
| :--- | :--- | :--- | :--- |
| VAPI Platform | ~$0.05/min | 3 min avg × 1000 | **$150** |
| OpenAI GPT-4o | ~$0.01/call | 1000 calls | **$10** |
| ElevenLabs TTS | Included in VAPI | — | $0 |
| Deepgram STT | Included in VAPI | — | $0 |
| Twilio Telephony | ~$0.02/min | 3 min avg × 1000 | **$60** |
| AWS Infra (ECS + SQS) | ~$0.01/call | 1000 calls | **$10** |
| **Total** | | | **~$230/morning** |

*Costs are approximate. VAPI bundles STT/TTS in its per-minute rate. Actual costs depend on call duration and model usage.*

