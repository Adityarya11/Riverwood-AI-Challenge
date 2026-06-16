> [!IMPORTANT]  
> **🚨 ARCHIVED & MIGRATED 🚨**
>
> This repository contains an early prototype of the voice agent that relied on external third-party APIs (Twilio/VAPI).
>
> I have since overhauled this entirely and architected a completely **self-hosted, distributed, high-concurrency runtime** built from the ground up in Go and Python. It features concurrent STT, LLM, and TTS inference with zero external API dependencies.
>
> 🚀 **Please visit the new, active project here:** [Voice Agent Runtime](https://github.com/Adityarya11/voice-agent-runtime)

# AI Voice Calling Agent

AI Voice Calling Agent is a low-latency, stateful, and concurrency-ready voice automation system for outbound customer conversations.
It combines telephony orchestration, streaming LLM responses, intent detection, and persistent conversational memory in a single pipeline.

## Architecture Workflow

```text
[ Admin / Scheduler ]
		  | (POST /trigger/{user_id})
		  v
+---------------------------------------------------+
|               FastAPI Orchestration Layer         |
|                                                   |
|  1. Fetches user + construction context           |
|  2. Determines returning vs new customer flow     |
|  3. Triggers outbound call (VAPI / Twilio)        |
|  4. Streams conversational responses              |
+---------------------------------------------------+
		  |                                 ^
		  | (Call + webhook events)         | (User speech / call events)
		  v                                 |
+---------------------------------------------------+
|             Telephony Runtime Layer               |
|         VAPI (Primary) + Twilio (Fallback)        |
+---------------------------------------------------+
		  |
		  v
[ Customer Phone ]

Parallel system components:
  - Redis Hot Memory for in-call context retrieval
  - SQLite Cold Storage for durable interaction history
  - Celery + Redis queue for concurrency and bulk dispatch workflows
```

## Core Highlights

- Stateful memory pipeline: hot and cold memory tiers preserve context across sessions.
- Streaming-first response path: token-level response generation for reduced perceived latency.
- Intent-aware conversation control: fast-path handling for busy/reject and site-visit confirmations.
- Fault-tolerant telephony strategy: primary and fallback call routes for operational resilience.
- Concurrency-oriented execution: queue-backed worker model for scalable outbound workloads.

## System Call Flow

```text
1. OUTBOUND TRIGGER
	Backend -> Initiates Call via VAPI/Twilio -> Plays Greeting

2. USER SPEECH LOOP
	Telephony layer captures user speech -> sends webhook payload to backend

3. FASTAPI PROCESSING PIPELINE
	|-- Check User Intent
	|   |-- IF "Busy / Not Interested": Stream canned response -> HANGUP
	|   |-- IF "Site Visit Confirmed": Update CRM flag -> Stream confirmation -> HANGUP
	|   |
	|-- IF Standard Conversation:
		 |-- Retrieve context from memory layer
		 |-- Build prompt with latest project + customer state
		 |-- Run LLM response generation (streaming where applicable)
		 |-- Return telephony-ready response

4. CONTINUATION
	Conversation loops until termination signal, then memory is committed to durable storage.
```

## Tech Stack

| Component         | Technology                            | Purpose                                                |
| :---------------- | :------------------------------------ | :----------------------------------------------------- |
| Backend Framework | FastAPI (Python)                      | API orchestration, webhook handling, call flow control |
| Telephony Layer   | VAPI + Twilio                         | Outbound calling, call events, speech capture          |
| LLM Engine        | OpenAI GPT-4o-mini                    | Contextual conversational response generation          |
| Text-to-Speech    | ElevenLabs (via VAPI) + gTTS fallback | Real-time voice output and fallback speech generation  |
| Memory Layer      | Redis + SQLite (SQLAlchemy)           | Hot context retrieval and persistent history           |
| Concurrency Layer | Celery + Redis                        | Queue-based worker execution and bulk dispatch         |
