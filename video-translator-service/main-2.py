import os
import uuid
import json
import shutil
import subprocess
import traceback
import logging
import re
import wave
from typing import List, Dict

from fastapi import FastAPI, UploadFile, File, Form, Response, HTTPException
from pydantic import BaseModel

# Google Cloud & Gen AI Imports
from google.cloud import storage
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech
from google.api_core.client_options import ClientOptions
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ads-translator-v4")

app = FastAPI()

# --- Configuration ---
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
STT_REGION = "us" 
LOCATION = "us-central1"
BUCKET_NAME = os.environ.get("BUCKET_NAME")

# Initialize Clients
storage_client = storage.Client()
speech_client = speech_v2.SpeechClient(
    client_options=ClientOptions(api_endpoint=f"{STT_REGION}-speech.googleapis.com")
)
genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

SUPPORTED_LANGUAGES = {
    "spanish": "Spanish", "es": "Spanish", "german": "German", "de": "German",
    "french": "French", "fr": "French", "japanese": "Japanese", "ja": "Japanese",
    "dutch": "Dutch", "nl": "Dutch", "english": "English"
}

# --- Helper Functions ---

def get_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

def save_wav(filename, pcm_data):
    # pylint: disable=no-member
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1) 
        wf.setsampwidth(2)
        wf.setframerate(24000) 
        wf.writeframes(pcm_data)

# --- Core Workflow ---

def process_translation_workflow(video_gcs_uri: str, target_lang_raw: str, job_id: str, temp_dir: str) -> str:
    try:
        target_lang_name = SUPPORTED_LANGUAGES.get(target_lang_raw.lower().strip(), target_lang_raw)
        
        local_video = f"{temp_dir}/input.mp4"
        download_blob(video_gcs_uri, local_video)
        total_video_duration = get_duration(local_video)
        
        # Extract audio for STT
        local_audio_extract = f"{temp_dir}/original_full.wav"
        subprocess.run(["ffmpeg", "-i", local_video, "-ac", "1", "-ar", "16000", local_audio_extract, "-y"], check=True, capture_output=True)
        
        audio_gcs_uri = f"gs://{BUCKET_NAME}/{job_id}/source.wav"
        upload_blob(local_audio_extract, BUCKET_NAME, f"{job_id}/source.wav")

        # PHASE 1: Ear (Chirp 3 Transcription)
        logger.info(f"[{job_id}] Phase 1: Chirp 3 Transcription...")
        transcripts = transcribe_chirp3(audio_gcs_uri)
        
        # PHASE 1.5: Director (Vibe Analysis)
        logger.info(f"[{job_id}] Phase 1.5: Detailed Emotion Analysis...")
        enriched_segments = analyze_video_vibes(video_gcs_uri, transcripts)
        
        # PHASE 2: Voice (Expressive Synthesis with Time-Sync)
        logger.info(f"[{job_id}] Phase 2: Generating Time-Synced Dubbing...")
        processed_segment_paths = []
        
        for i, seg in enumerate(enriched_segments):
            # 1. Generate Raw Spanish Audio
            audio_data = synthesize_gemini_25_tts(seg['text'], target_lang_name, seg['style_instruction'])
            raw_seg_path = f"{temp_dir}/raw_seg_{i}.wav"
            save_wav(raw_seg_path, audio_data)
            
            # 2. Time-Stretch Logic to fit the original window exactly
            original_window = max(0.5, seg['end_offset'] - seg['start_offset'])
            actual_tts_duration = get_duration(raw_seg_path)
            
            # Calculate stretch ratio (e.g., 1.2 means the Spanish is too long, must speed up)
            stretch_ratio = actual_tts_duration / original_window
            # Clamp ratio between 0.5x and 2.0x to avoid sounding like a chipmunk
            stretch_ratio = max(0.5, min(2.0, stretch_ratio))
            
            synced_seg_path = f"{temp_dir}/synced_seg_{i}.wav"
            # Use 'atempo' filter: changes speed WITHOUT changing pitch
            subprocess.run([
                "ffmpeg", "-i", raw_seg_path, 
                "-filter:a", f"atempo={stretch_ratio}", 
                synced_seg_path, "-y"
            ], check=True, capture_output=True)
            
            processed_segment_paths.append({"path": synced_seg_path, "start": seg['start_offset']})

        # --- PHASE 3: Assembly (The Silence Sandwich) ---
        logger.info(f"[{job_id}] Phase 3: Building the complete Spanish Voice stem...")
        voice_stem = f"{temp_dir}/spanish_voice_stem.wav"
        # Create silent track exactly the length of the video
        subprocess.run(["ffmpeg", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={total_video_duration}", "-y", voice_stem], check=True, capture_output=True)
        
        temp_mix = f"{temp_dir}/current_mix.wav"
        for seg in processed_segment_paths:
            delay_ms = int(seg['start'] * 1000)
            subprocess.run([
                "ffmpeg", "-i", voice_stem, "-i", seg['path'],
                "-filter_complex", f"[1]adelay={delay_ms}|{delay_ms}[v];[0][v]amix=inputs=2:duration=first:dropout_transition=0",
                "-y", temp_mix
            ], check=True, capture_output=True)
            os.replace(temp_mix, voice_stem)

        # --- PHASE 4: Pro Mixing (Substitute Speech, Keep Background) ---
        logger.info(f"[{job_id}] Phase 4: Mixing (Suppressing English, Preserving Music)...")
        final_output_local = f"{temp_dir}/final_ad.mp4"
        
        # FFmpeg Filter Logic:
        # 1. Take Original Audio [0:a]
        # 2. Take New Spanish Voice [1:a]
        # 3. Use 'sidechaincompress' to MUTE the original audio whenever the Spanish voice is active.
        # 4. Mix them together.
        mix_filter = (
            "[1:a]asplit=2[voice_for_mix][voice_for_trigger];"
            "[0:a][voice_for_trigger]sidechaincompress=threshold=0.01:ratio=20:attack=10:release=200[bg_ducked];"
            "[bg_ducked][voice_for_mix]amix=inputs=2:duration=first:weights='1 1'[outa]"
        )
        
        subprocess.run([
            "ffmpeg", "-i", local_video, "-i", voice_stem,
            "-filter_complex", mix_filter,
            "-map", "0:v:0", "-map", "[outa]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-y", final_output_local
        ], check=True, capture_output=True)
        
        # Upload results
        upload_blob(voice_stem, BUCKET_NAME, f"debug/{job_id}/voice_only.wav")
        output_gcs_path = f"output/{job_id}/translated_ad.mp4"
        upload_blob(final_output_local, BUCKET_NAME, output_gcs_path)
        
        return f"gs://{BUCKET_NAME}/{output_gcs_path}"

    except Exception as e:
        logger.error(f"WORKFLOW CRASHED: {str(e)}\n{traceback.format_exc()}")
        raise e

# --- Specialized Helpers ---

def transcribe_chirp3(gcs_uri: str) -> List[Dict]:
    recognition_config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=["en-US"],
        model="chirp_3", 
        features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
    )
    request = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{PROJECT_ID}/locations/{STT_REGION}/recognizers/_",
        config=recognition_config,
        uri=gcs_uri,
    )
    response = speech_client.recognize(request=request)
    
    segments = []
    for result in response.results:
        if not result.alternatives: continue
        alt = result.alternatives[0]
        segments.append({
            "text": alt.transcript,
            "start_offset": alt.words[0].start_offset.total_seconds(),
            "end_offset": alt.words[-1].end_offset.total_seconds()
        })
    return segments

def analyze_video_vibes(video_uri: str, segments: List[Dict]) -> List[Dict]:
    """Uses Gemini 2.5 Pro for deep prosodic analysis."""
    context = [{"start": s['start_offset'], "text": s['text']} for s in segments]
    prompt = f"""
    Watch this video and analyze the speaker's emotional tone for these segments: {json.dumps(context)}
    Provide a detailed 'style_instruction' for each line. 
    Notes should include emotion, energy, and pitch (e.g., 'excited and high-pitched for an ad', 'serious and calm').
    Return a JSON list of objects: [{{"style_instruction": "..."}}, ...]
    """
    video_part = types.Part.from_uri(file_uri=video_uri, mime_type="video/mp4")
    response = genai_client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[video_part, prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    style_results = json.loads(response.text)
    for i, seg in enumerate(segments):
        seg['style_instruction'] = style_results[i].get('style_instruction', 'speak naturally')
    return segments

def synthesize_gemini_25_tts(english_text: str, target_lang: str, director_note: str) -> bytes:
    # Use the Gemini TTS prompt to handle translation and emotion simultaneously
    prompt = f"ACT AS A TRANSLATOR. Translate this text into {target_lang}: '{english_text}'. Then, speak the translation in {target_lang} with a style that is {director_note}."
    
    response = genai_client.models.generate_content(
        model="gemini-2.5-pro-tts",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Aoede')
                )
            )
        )
    )
    return response.candidates[0].content.parts[0].inline_data.data

# --- Standard GCS Utilities ---

def download_blob(u, p):
    m = re.match(r'gs://([^/]+)/(.+)', u)
    storage_client.bucket(m.group(1)).blob(m.group(2)).download_to_filename(p)

def upload_blob(p, b, n):
    storage_client.bucket(b).blob(n).upload_from_filename(p)

@app.post("/translate-raw")
async def translate_video_raw(file: UploadFile = File(...), target_language: str = Form(...)):
    job_id = str(uuid.uuid4())
    temp_dir = f"/tmp/{job_id}"
    os.makedirs(temp_dir, exist_ok=True)
    try:
        input_local = f"{temp_dir}/raw.mp4"
        with open(input_local, "wb") as b: shutil.copyfileobj(file.file, b)
        input_uri = f"gs://{BUCKET_NAME}/{job_id}/raw.mp4"
        upload_blob(input_local, BUCKET_NAME, f"{job_id}/raw.mp4")
        final_uri = process_translation_workflow(input_uri, target_language, job_id, temp_dir)
        output_local = f"{temp_dir}/out.mp4"
        download_blob(final_uri, output_local)
        with open(output_local, "rb") as f: return Response(content=f.read(), media_type="video/mp4")
    except Exception as e: return Response(content=str(e).encode(), status_code=500)
    finally: shutil.rmtree(temp_dir, ignore_errors=True)