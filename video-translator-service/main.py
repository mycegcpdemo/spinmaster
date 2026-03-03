# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Video Translator Service (Cloud Run)

This module represents the core translation sidecar service for the Spinmaster Multimodal
Architecture. It serves as an HTTP API backend, deployed via Google Cloud Run, to handle
compute-intensive tasks such as video transcription, audio separation (using Demucs), 
sentiment analysis, and Gemini 2.5 Pro based TTS generation.

It interfaces with Google Cloud Storage for handling large payloads and uses Secret Manager
exclusively for retrieving configuration data. The outputs of this service are utilized
by upstream agent engines (like `video_agent`).
"""

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

# Google Cloud Imports
from google.cloud import storage, speech_v2, secretmanager
from google.cloud.speech_v2.types import cloud_speech
from google.api_core.client_options import ClientOptions
from google.cloud import translate_v3

# NEW: Google Gen AI SDK (2026 unified client)
from google import genai
from google.genai import types

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ads-translator-final")

app = FastAPI()

# --- Secret Manager Helper ---
def get_secret(secret_id: str, project_id: str, version_id: str = "latest") -> str:
    """Fetch secret from Google Cloud Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

# --- Configuration ---
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
STT_REGION = "us" 
LOCATION = "us-central1"
# Fetch credentials/configs exclusively from Secret Manager
try:
    BUCKET_NAME = get_secret("bucket-name-secret", PROJECT_ID)
except Exception as e:
    logger.error(f"Failed to load BUCKET_NAME from Secret Manager: {e}")
    raise RuntimeError("Critical Secret Missing: bucket-name-secret")

# Initialize Clients
storage_client = storage.Client()
speech_client = speech_v2.SpeechClient(
    client_options=ClientOptions(api_endpoint=f"{STT_REGION}-speech.googleapis.com")
)
translate_client = translate_v3.TranslationServiceClient()
genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

# --- Language Dictionary ---
SUPPORTED_LANGUAGES = {
    "spanish": "Spanish", "es": "Spanish", "german": "German", "de": "German",
    "french": "French", "fr": "French", "japanese": "Japanese", "ja": "Japanese",
    "dutch": "Dutch", "nl": "Dutch", "english": "English"
}

# --- Helper Functions ---

def get_duration(file_path):
    """
    Retrieves the duration of a media file utilizing ffprobe.
    
    Args:
        file_path (str): The local filesystem path to the media file.
        
    Returns:
        float: The duration of the media file in seconds.
    """
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

def separate_audio_demucs(input_audio, temp_dir):
    """
    Separates audio stems utilizing the Demucs AI model.
    
    This function isolates the vocal track (typically the original narrator) from
    the background music and ambient noise. The resulting background stem is later
    merged with the new AI-generated translated voiceover to create a natural-sounding
    final audio mix.
    
    Args:
        input_audio (str): Path to the input audio file.
        temp_dir (str): Directory to store intermediate separated stem files.
        
    Returns:
        str: The filesystem path to the isolated background audio stem ('no_vocals').
    """
    output_base = os.path.join(temp_dir, "separated")
    logger.info("Separating audio stems using Demucs...")
    subprocess.run([
        "demucs", "-n", "htdemucs_ft", "--two-stems", "vocals", 
        "-o", output_base, "-d", "cpu", input_audio
    ], check=True)
    filename_no_ext = os.path.splitext(os.path.basename(input_audio))[0]
    # 'no_vocals' contains the background music and ambient sounds
    bg_path = os.path.join(output_base, "htdemucs_ft", filename_no_ext, "no_vocals.wav")
    return bg_path

def save_wav(filename, pcm_data):
    # pylint: disable=no-member
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1) 
        wf.setsampwidth(2)
        wf.setframerate(24000) 
        wf.writeframes(pcm_data)

# --- The "Director" Phase ---

def analyze_video_vibes(video_uri: str, segments: List[Dict]) -> List[Dict]:
    """Uses Gemini 2.5 Pro to write detailed performance notes for the TTS model."""
    context = [{"start": s['start_offset'], "text": s['text']} for s in segments]
    
    prompt = f"""
    You are a professional Voice-Over Director. Analyze the speaker in this video for these segments:
    {json.dumps(context)}

    For each segment, provide a natural language 'style_instruction' describing their exact tone.
    Capture the highs, lows, and excitement. 
    Examples: 'shouting with high-energy excitement for a sale', 'whispering warmly and slowly', 'cheerful and energetic'.
    
    Return a JSON list of objects: [{{"style_instruction": "..."}}, ...]
    """
    
    video_part = types.Part.from_uri(file_uri=video_uri, mime_type="video/mp4")
    
    try:
        response = genai_client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[video_part, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        style_results = json.loads(response.text)
        for i, seg in enumerate(segments):
            seg['style_instruction'] = style_results[i].get('style_instruction', 'speak naturally')
        return segments
    except Exception as e:
        logger.warning(f"Vision analysis failed: {e}. Defaulting to 'Natural'.")
        for seg in segments: seg['style_instruction'] = 'speak naturally'
        return segments

# --- The "Voice" Phase ---

def synthesize_gemini_25_tts(english_text: str, target_lang: str, director_note: str) -> bytes:
    """Uses the latest Gemini 2.5 Pro TTS model for high-impact Ad speech."""
    prompt = f"ACT AS A TRANSLATOR. Translate '{english_text}' to {target_lang} and say it {director_note}."
    
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

# --- Transcription (Ears) ---

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

# --- Workflow Orchestrator ---

def process_translation_workflow(video_gcs_uri: str, target_lang_raw: str, job_id: str, temp_dir: str) -> str:
    try:
        target_lang_name = SUPPORTED_LANGUAGES.get(target_lang_raw.lower().strip(), target_lang_raw)
        logger.info(f"[{job_id}] Workflow started for {target_lang_name}")
        
        local_video = f"{temp_dir}/input.mp4"
        download_blob(video_gcs_uri, local_video)
        total_video_duration = get_duration(local_video)
        
        # 1. Background Isolation (Keeping the noise, losing the English)
        full_audio = f"{temp_dir}/original_full.wav"
        subprocess.run(["ffmpeg", "-i", local_video, "-ac", "2", "-ar", "44100", full_audio, "-y"], check=True, capture_output=True)
        clean_background_track = separate_audio_demucs(full_audio, temp_dir)

        # 2. Transcription (The Ears)
        stt_audio = f"{temp_dir}/stt_ready.wav"
        subprocess.run(["ffmpeg", "-i", full_audio, "-ac", "1", "-ar", "16000", stt_audio, "-y"], check=True)
        upload_blob(stt_audio, BUCKET_NAME, f"{job_id}/stt.wav")
        transcripts = transcribe_chirp3(f"gs://{BUCKET_NAME}/{job_id}/stt.wav")
        
        # 3. Emotion Analysis (The Director)
        enriched_segments = analyze_video_vibes(video_gcs_uri, transcripts)
        
        # 4. Synthesis & Time-Alignment (The Voice)
        processed_segment_paths = []
        for i, seg in enumerate(enriched_segments):
            audio_data = synthesize_gemini_25_tts(seg['text'], target_lang_name, seg['style_instruction'])
            raw_seg_path = f"{temp_dir}/raw_seg_{i}.wav"
            save_wav(raw_seg_path, audio_data)
            
            # --- CRITICAL SYNC FIX ---
            original_window = max(0.4, seg['end_offset'] - seg['start_offset'])
            actual_tts_duration = get_duration(raw_seg_path)
            stretch_ratio = actual_tts_duration / original_window
            
            synced_seg_path = f"{temp_dir}/synced_seg_{i}.wav"
            subprocess.run([
                "ffmpeg", "-i", raw_seg_path, 
                "-filter:a", f"atempo={max(0.5, min(2.0, stretch_ratio))}", 
                synced_seg_path, "-y"
            ], check=True, capture_output=True)
            processed_segment_paths.append({"path": synced_seg_path, "start": seg['start_offset']})

        # 5. Assembly of Translated Stem
        voice_stem = f"{temp_dir}/translated_voice_stem.wav"
        subprocess.run(["ffmpeg", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={total_video_duration}", "-y", voice_stem], check=True, capture_output=True)
        
        temp_mix = f"{temp_dir}/current_mix.wav"
        for seg in processed_segment_paths:
            delay_ms = int(seg['start'] * 1000)
            subprocess.run(["ffmpeg", "-i", voice_stem, "-i", seg['path'], "-filter_complex", f"[1]adelay={delay_ms}|{delay_ms}[v];[0][v]amix=inputs=2:duration=first", "-y", temp_mix], check=True, capture_output=True)
            os.replace(temp_mix, voice_stem)

        # 6. Final Production: Merge Translated Voice + Isolated Background
        final_audio_mix = f"{temp_dir}/final_mix.wav"
        subprocess.run([
            "ffmpeg", "-i", voice_stem, "-i", clean_background_track,
            "-filter_complex", "amix=inputs=2:duration=first:weights='2 1'", 
            final_audio_mix, "-y"
        ], check=True, capture_output=True)

        final_output_local = f"{temp_dir}/final_ad.mp4"
        # -map 0:v:0 -> Use visuals from source
        # -map 1:a:0 -> Use our new mixed audio (English is now gone)
        subprocess.run([
            "ffmpeg", "-i", local_video, "-i", final_audio_mix,
            "-map", "0:v:0", "-map", "1:a:0", 
            "-c:v", "copy", "-c:a", "aac", "-shortest", "-y", final_output_local
        ], check=True, capture_output=True)
        
        output_gcs_path = f"output/{job_id}/translated_ad.mp4"
        upload_blob(final_output_local, BUCKET_NAME, output_gcs_path)
        return f"gs://{BUCKET_NAME}/{output_gcs_path}"

    except Exception as e:
        logger.error(f"WORKFLOW CRASHED: {str(e)}\n{traceback.format_exc()}")
        raise e

# --- Cloud Run Infrastructure ---

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