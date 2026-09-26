# GAIA Audio System Troubleshooting Guide

This guide helps diagnose and fix audio-related issues with GAIA's voice features (talk mode, voice chat, etc.).

## Quick Diagnostics

### Test Whisper ASR Module Directly

Test the Whisper ASR module with streaming:

```bash
python src/gaia/audio/whisper_asr.py --stream --duration 20
```

## Logging and Verbosity

- By default, many detailed pipeline messages now log at DEBUG level to keep the console clean during normal use.
- Use `--logging-level DEBUG` to see low-level audio processing details, including:
  - Audio device selection and stream start
  - Per-chunk enqueue events and energies
  - Batch processing cycles
  - Per-segment transcription text
  - ASR/LLM coordination events during TTS streaming
- For minimal noise, run with `--logging-level WARNING`.

## Common Issues & Solutions

### Issue 1: No Audio Detected

**Symptoms:**
- Energy levels in the DEBUG log show 0.000000 or very low values (< 0.0001)

**Solutions:**
1. **Check Windows Settings:**
   - Open Sound Settings → Recording
   - Right-click your microphone → Properties → Levels
   - Set volume to 70-100%
   - Ensure "Microphone Boost" is enabled if available

2. **Check Microphone Permissions:**
   - Windows Settings → Privacy → Microphone
   - Ensure Python/Terminal has microphone access

3. **Select Correct Device:**
   ```bash
   # List all audio devices
   python -c "import sounddevice as sd; [print(f'{d[\"index\"]}: {d[\"name\"]}') for d in sd.query_devices() if d.get('max_input_channels', 0) > 0]"

   # Use specific device (replace 0 with your device number)
   gaia talk --audio-device-index 0 --no-tts
   ```

### Issue 2: Audio Detected but No Transcription

**Symptoms:**
- DEBUG log shows speech-level energy when speaking
- Talk mode shows "Listening..." but never transcribes
- No text output despite speaking

**Solutions:**
1. **Voice Detection Threshold:**
   - The system may be too strict about what counts as "speech"
  - The internal VAD (amplitude) threshold in `WhisperAsr` defaults to ~0.01, tuned for typical speaking levels (0.02–0.03)
  - The CLI flag `--silence-threshold` controls pause duration (in seconds) before sending the last heard phrase to the LLM, not the amplitude threshold
  - If detection is unreliable, check the per-chunk energy values in the DEBUG log and reduce background noise

2. **Use Smaller Whisper Model:**
   ```bash
   # Tiny model is fastest and works well for testing
   gaia talk --whisper-model-size tiny --no-tts
   ```

3. **Enable Debug Logging:**
   ```bash
   gaia talk --no-tts --logging-level DEBUG
   ```
   Look for messages like:
   - `Chunk X: energy=0.00XXXX, is_speech=True/False`
   - `Adding speech to queue: XXXXX samples`
   - `Transcribed: your text here`

### Issue 3: Poor Transcription Quality

**Symptoms:**
- Repeated text ("I'm sorry, I'm sorry...")
- Nonsensical transcriptions
- Words cut off mid-sentence

**Solutions:**
1. **Use Better Whisper Model:**
   ```bash
   # Small model for better accuracy
   gaia talk --whisper-model-size small
   
   # Medium model for best accuracy (slower)
   gaia talk --whisper-model-size medium
   ```

2. **Speak Clearly:**
   - Speak in complete sentences
   - Pause briefly between sentences
   - Avoid background noise

3. **Enable CUDA (if available):**
   ```bash
   gaia talk --whisper-model-size small --cuda
   ```

### Issue 4: TTS Not Working

**Symptoms:**
- No voice response from GAIA
- On older builds only: `'AudioClient' object has no attribute 'tts_client'`

**Solutions:**
1. **Disable TTS for Testing:**
   ```bash
   gaia talk --no-tts
   ```

2. **Check Kokoro TTS Installation:**
   - Kokoro TTS loads a model on first use which can be slow
   - Wait for the model to download (shows progress)
   - A warning like "Defaulting repo_id to hexgrad/Kokoro-82M" is harmless

3. **Programmatic TTS (optional):**
   - If needed in code, you can call `AudioClient.speak_text("Hello")` after TTS has been initialized

## Debug Commands

### Full Debug Mode
```bash
# Maximum debug output
gaia talk --no-tts --logging-level DEBUG --whisper-model-size tiny
```

### Test Individual Components

**Test Real-time Streaming:**
   ```bash
   # Test the WhisperAsr module directly with streaming
   python src/gaia/audio/whisper_asr.py --stream --duration 20
   ```

## Audio System Architecture

```
Microphone → sounddevice → AudioRecorder → Voice Activity Detection →
Audio Queue → WhisperAsr → Transcription Queue → LLM → Response
```

### Key Components:

1. **AudioRecorder** (`audio_recorder.py`):
   - Captures raw audio from microphone
   - Detects speech vs silence
   - Buffers audio segments

2. **WhisperAsr** (`whisper_asr.py`):
   - Loads OpenAI Whisper model
   - Processes audio chunks
   - Returns transcribed text

3. **AudioClient** (`audio_client.py`):
   - Orchestrates recording and transcription
   - Handles TTS responses
   - Manages voice chat sessions

## Performance Tips

1. **Model Selection:**
   - `tiny`: Fastest, ~39MB, good for testing
   - `base`: Balanced, ~74MB, good quality
   - `small`: Better accuracy, ~244MB
   - `medium`: Best accuracy, ~769MB, slower

2. **Reduce Latency:**
   - Use `--no-tts` to skip text-to-speech
   - Use smaller models (`tiny` or `base`)
   - Enable CUDA with `--cuda` if you have GPU

3. **Improve Accuracy:**
   - Use larger models (`small` or `medium`)
   - Ensure good microphone placement
   - Minimize background noise
   - Speak clearly and at consistent volume

## Environment Variables

```bash
# Set default audio device (optional)
export GAIA_AUDIO_DEVICE=1

# Set default Whisper model
export GAIA_WHISPER_MODEL=small

# Enable debug logging globally
export GAIA_LOG_LEVEL=DEBUG
```

## Still Having Issues?

1. **Verify Python Environment:**
   ```bash
   python --version  # Should be 3.8+
   pip show whisper  # Should be installed
   pip show sounddevice  # Should be installed
   ```

2. **Check System Audio:**
   - Test microphone in another app (Voice Recorder, Discord, etc.)
   - Ensure no other app is using the microphone exclusively
   - Restart audio service: `net stop audiosrv && net start audiosrv` (Admin)

3. **File an Issue:**
   If problems persist, create an issue with:
   - Output of the Whisper ASR streaming test above
   - Debug logs from `gaia talk --logging-level DEBUG`
   - Your system info (Windows version, Python version)
   - Audio device list from the diagnostic commands

## Quick Start After Troubleshooting

Once your microphone is working:

```bash
# Basic voice chat
gaia talk

# Voice chat without TTS (text-only responses)
gaia talk --no-tts

# Voice chat with specific model
gaia talk --whisper-model-size small

# Voice chat with specific microphone
gaia talk --audio-device-index 1
```

---

For more information, see the main GAIA documentation at `/docs/talk.md`
