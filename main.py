from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import requests
import shutil
import assemblyai as aai
import os
from google import genai
from pydantic import BaseModel
from pydub import AudioSegment
import tempfile
import time
import json
from datetime import datetime

# Load environment variables from .env
load_dotenv()

app = FastAPI()

# Mount uploads, static and template directories
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CHAT_HISTORY_FILE = "chat_history.json"
chat_histories = {}

def load_chat_histories():
    global chat_histories
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            chat_histories = json.load(f)
    except FileNotFoundError:
        chat_histories = {}

def save_chat_histories():
    with open(CHAT_HISTORY_FILE, "w") as f:
        json.dump(chat_histories, f, indent=2)

load_chat_histories()

MURF_API_KEY = os.getenv("MURF_API_KEY")
ASSEMBLY_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client()
aai.settings.api_key = ASSEMBLY_API_KEY
transcriber = aai.Transcriber()

FALLBACK_AUDIO_URL = "/static/fallback.mp3"
FALLBACK_MESSAGE = "I'm having trouble connecting right now."

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/tts")
async def tts(text: str = Form(...), voice_id: str = Form("en-us-natalie")):
    if not MURF_API_KEY:
        return JSONResponse(status_code=500, content={"error": "API key not configured."})

    url = "https://api.murf.ai/v1/speech/generate"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "api-key": MURF_API_KEY
    }
    payload = {
        "text": text,
        "voiceId": voice_id,
        "format": "MP3",
        "sampleRate": 24000,
        "channelType": "STEREO"
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            audio_url = data.get("audioFile") or data.get("audio_url") or data.get("url")
            if audio_url:
                return {"audio_url": audio_url}
            else:
                # fallback
                return {
                    "audio_url": FALLBACK_AUDIO_URL,
                    "message": FALLBACK_MESSAGE
                }
        else:
            return {
                "audio_url": FALLBACK_AUDIO_URL,
                "message": FALLBACK_MESSAGE
            }
    except Exception:
        return {
            "audio_url": FALLBACK_AUDIO_URL,
            "message": FALLBACK_MESSAGE
        }

@app.get("/voices")
async def get_voices():
    if not MURF_API_KEY:
        return JSONResponse(status_code=500, content={"error": "API key not configured."})
    url = "https://api.murf.ai/v1/speech/voices"
    headers = {
        "Accept": "application/json",
        "api-key": MURF_API_KEY
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            return JSONResponse(status_code=500, content={"error": "Failed to fetch voices", "details": response.text})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/upload-audio/")
async def upload_audio(file: UploadFile = File(...)):
    os.makedirs("uploads", exist_ok=True)
    file_path = os.path.join("uploads", file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": os.path.getsize(file_path)
    }

@app.post("/transcribe/file")
async def transcribe_file(file: UploadFile = File(...)):
    try:
        audio_bytes = await file.read()
        transcript = transcriber.transcribe(audio_bytes)
        return {"transcript": transcript.text}
    except Exception:
        return {
            "transcript": FALLBACK_MESSAGE,
            "audio_url": FALLBACK_AUDIO_URL
        }

@app.post("/tts/echo")
async def tts_echo(file: UploadFile = File(...), voice_id: str = Form("en-US-ken")):
    if not MURF_API_KEY or not ASSEMBLY_API_KEY:
        return JSONResponse(status_code=500, content={"error": "API key not configured."})

    try:
        audio_bytes = await file.read()
        transcript = transcriber.transcribe(audio_bytes)
        if not transcript.text:
            raise Exception("Transcription failed")

        url = "https://api.murf.ai/v1/speech/generate"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": MURF_API_KEY
        }
        payload = {
            "text": transcript.text,
            "voiceId": voice_id,
            "format": "MP3",
            "sampleRate": 24000,
            "channelType": "STEREO"
        }

        tts_response = requests.post(url, json=payload, headers=headers)
        if tts_response.status_code != 200:
            raise Exception("Murf TTS failed")

        data = tts_response.json()
        audio_url_from_murf = data.get("audioFile") or data.get("audio_url") or data.get("url")
        if not audio_url_from_murf:
            raise Exception("No audio URL in Murf response")

        audio_data = requests.get(audio_url_from_murf)
        if audio_data.status_code != 200:
            raise Exception("Failed to download audio from Murf")

        os.makedirs("uploads", exist_ok=True)
        local_filename = f"echo_{os.path.splitext(file.filename)[0]}.mp3"
        local_path = os.path.join("uploads", local_filename)
        with open(local_path, "wb") as f:
            f.write(audio_data.content)

        return {
            "audio_url": f"/uploads/{local_filename}",
            "transcript": transcript.text
        }
    except Exception:
        return {
            "audio_url": FALLBACK_AUDIO_URL,
            "transcript": FALLBACK_MESSAGE
        }

class LLMQuery(BaseModel):
    text: str

@app.post("/llm/query")
async def llm_query_audio(file: UploadFile = File(...), voice_id: str = Form("en-US-ken")):
    if not (GEMINI_API_KEY and MURF_API_KEY and ASSEMBLY_API_KEY):
        return JSONResponse(status_code=500, content={"error": "One or more API keys not configured."})

    try:
        audio_bytes = await file.read()
        transcript = transcriber.transcribe(audio_bytes)
        if not transcript.text:
            raise Exception("Transcription failed")
        user_text = transcript.text

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text
        )
        llm_text = response.text.strip() if hasattr(response, "text") else ""
        if not llm_text:
            raise Exception("LLM returned no text")

        def chunk_text(text, limit=3000):
            chunks = []
            while len(text) > limit:
                split_at = text.rfind(" ", 0, limit)
                if split_at == -1:
                    split_at = limit
                chunks.append(text[:split_at])
                text = text[split_at:].lstrip()
            if text:
                chunks.append(text)
            return chunks

        chunks = chunk_text(llm_text)
        combined_audio = AudioSegment.silent(duration=0)
        for idx, chunk in enumerate(chunks):
            murf_url = "https://api.murf.ai/v1/speech/generate"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api-key": MURF_API_KEY
            }
            payload = {
                "text": chunk,
                "voiceId": voice_id,
                "format": "MP3",
                "sampleRate": 24000,
                "channelType": "STEREO"
            }
            murf_res = requests.post(murf_url, json=payload, headers=headers)
            if murf_res.status_code != 200:
                raise Exception(f"Murf TTS failed on chunk {idx+1}")

            murf_data = murf_res.json()
            audio_url_from_murf = murf_data.get("audioFile") or murf_data.get("audio_url") or murf_data.get("url")
            if not audio_url_from_murf:
                raise Exception(f"No audio URL from Murf on chunk {idx+1}")

            audio_data = requests.get(audio_url_from_murf)
            if audio_data.status_code != 200:
                raise Exception(f"Failed to download Murf audio chunk {idx+1}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(audio_data.content)
                tmp_path = tmp.name
            combined_audio += AudioSegment.from_mp3(tmp_path)

        os.makedirs("uploads", exist_ok=True)
        output_filename = f"llm_response_{int(time.time())}.mp3"
        output_path = os.path.join("uploads", output_filename)
        combined_audio.export(output_path, format="mp3")

        return {
            "transcript": user_text,
            "llm_text": llm_text,
            "audio_url": f"/uploads/{output_filename}"
        }

    except Exception:
        return {
            "audio_url": FALLBACK_AUDIO_URL,
            "transcript": FALLBACK_MESSAGE,
            "llm_text": FALLBACK_MESSAGE
        }

@app.post("/agent/chat/{session_id}")
async def agent_chat(session_id: str, file: UploadFile = File(...), voice_id: str = Form("en-US-ken")):
    if not (GEMINI_API_KEY and MURF_API_KEY and ASSEMBLY_API_KEY):
        return JSONResponse(status_code=500, content={"error": "One or more API keys not configured."})

    try:
        audio_bytes = await file.read()
        transcript = transcriber.transcribe(audio_bytes)
        if not transcript.text:
            raise Exception("Transcription failed")
        user_text = transcript.text

        if session_id not in chat_histories:
            chat_histories[session_id] = []

        chat_histories[session_id].append({
            "role": "user",
            "content": user_text,
            "timestamp": datetime.utcnow().isoformat()
        })
        save_chat_histories()

        def build_prompt(history):
            pieces = []
            for msg in history:
                if msg["role"] == "user":
                    pieces.append("User: " + msg["content"])
                else:
                    pieces.append("Assistant: " + msg["content"])
            pieces.append("Assistant:")
            return "\n".join(pieces)

        prompt = build_prompt(chat_histories[session_id])

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        llm_text = response.text.strip() if hasattr(response, "text") else ""
        if not llm_text:
            raise Exception("LLM returned no text")

        chat_histories[session_id].append({
            "role": "assistant",
            "content": llm_text,
            "timestamp": datetime.utcnow().isoformat()
        })
        save_chat_histories()

        def chunk_text(text, limit=3000):
            chunks = []
            while len(text) > limit:
                split_at = text.rfind(" ", 0, limit)
                if split_at == -1:
                    split_at = limit
                chunks.append(text[:split_at])
                text = text[split_at:].lstrip()
            if text:
                chunks.append(text)
            return chunks

        chunks = chunk_text(llm_text)
        combined_audio = AudioSegment.silent(duration=0)

        for idx, chunk in enumerate(chunks):
            murf_url = "https://api.murf.ai/v1/speech/generate"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api-key": MURF_API_KEY
            }
            payload = {
                "text": chunk,
                "voiceId": voice_id,
                "format": "MP3",
                "sampleRate": 24000,
                "channelType": "STEREO"
            }
            murf_res = requests.post(murf_url, json=payload, headers=headers)
            if murf_res.status_code != 200:
                raise Exception(f"Murf TTS failed on chunk {idx+1}")

            murf_data = murf_res.json()
            audio_url_from_murf = murf_data.get("audioFile") or murf_data.get("audio_url") or murf_data.get("url")
            if not audio_url_from_murf:
                raise Exception(f"No audio URL from Murf on chunk {idx+1}")

            audio_data = requests.get(audio_url_from_murf)
            if audio_data.status_code != 200:
                raise Exception(f"Failed to download Murf audio chunk {idx+1}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(audio_data.content)
                tmp_path = tmp.name
            combined_audio += AudioSegment.from_mp3(tmp_path)

        os.makedirs("uploads", exist_ok=True)
        output_filename = f"agent_response_{session_id}_{int(time.time())}.mp3"
        output_path = os.path.join("uploads", output_filename)
        combined_audio.export(output_path, format="mp3")

        return {
            "transcript": user_text,
            "response_text": llm_text,
            "audio_url": f"/uploads/{output_filename}",
            "history": chat_histories[session_id]
        }

    except Exception:
        # On any failure, return fallback audio and message
        return {
            "transcript": FALLBACK_MESSAGE,
            "response_text": FALLBACK_MESSAGE,
            "audio_url": FALLBACK_AUDIO_URL,
            "history": chat_histories.get(session_id, [])
        }

@app.get("/agent/history/{session_id}")
async def get_chat_history(session_id: str):
    return {"history": chat_histories.get(session_id, [])}

@app.delete("/agent/history/{session_id}")
async def delete_chat_history(session_id: str):
    if session_id in chat_histories:
        del chat_histories[session_id]
        save_chat_histories()
        return {"message": "Chat history deleted successfully."}
    else:
        return JSONResponse(status_code=404, content={"error": "Session ID not found"})
