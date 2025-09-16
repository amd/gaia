# GAIA Audio System Troubleshooting Guide

This guide helps diagnose and fix audio-related issues with GAIA's voice features (talk mode, voice chat, etc.).

## Test Utilities

The `tests/` folder contains diagnostic tools for troubleshooting:

- **`test_mic_simple.py`** - Basic microphone test showing real-time audio levels
- **`test_talk_basic.py`** - Simple 3-second recording and transcription loop  
- **`test_audio_pipeline.py`** - Comprehensive test of all audio components

## Quick Diagnostics

### 1. Test Your Microphone (Basic Level)

First, verify your microphone is working at the hardware level:

```bash
# Run the simple microphone test
python src/gaia/audio/tests/test_mic_simple.py
```

**Expected Output:** You should see energy bars (█████) when you speak:
```
███████████                                        0.011151
█████████████████                                  0.017035
██████████████████████████                         0.026203
```

If you see only "(silence)" or very low values, your microphone isn't being captured properly.

### 2. Test Basic Whisper Transcription

Test if Whisper can transcribe your audio in a simple loop:

```bash
# Run the basic talk test (3-second recordings)
python src/gaia/audio/tests/test_talk_basic.py
```

This will record 3 seconds of audio, transcribe it, and show the result. You should see:
```
Recording for 3 seconds... SPEAK NOW!
Audio captured: 49152 samples, energy: 0.025000
Transcribing...
✅ TRANSCRIBED: Hello, this is a test
```

### 3. Comprehensive Audio Pipeline Test

For a complete diagnostic of all components:

```bash
# Run the full audio pipeline test
python src/gaia/audio/tests/test_audio_pipeline.py
```

This tests:
- Basic microphone functionality
- AudioRecorder class with voice activity detection  
- Raw recording visualization
- Full WhisperAsr integration

### 4. Test Whisper ASR Module Directly

Test the Whisper ASR module with streaming:

```bash
python src/gaia/audio/whisper_asr.py --mode mic --stream --duration 20
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
- Energy levels show 0.000000 or very low values (< 0.0001)
- No energy bars appear when speaking

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
   python -c "import pyaudio; p=pyaudio.PyAudio(); [print(f'{i}: {p.get_device_info_by_index(i)[\"name\"]}') for i in range(p.get_device_count()) if p.get_device_info_by_index(i).get('maxInputChannels')>0]; p.terminate()"
   
   # Use specific device (replace 0 with your device number)
   gaia talk --audio-device-index 0 --no-tts
   ```

### Issue 2: Audio Detected but No Transcription

**Symptoms:**
- Energy bars appear when speaking
- Talk mode shows "Listening..." but never transcribes
- No text output despite speaking

**Solutions:**
1. **Voice Detection Threshold:**
   - The system may be too strict about what counts as "speech"
  - The internal VAD (amplitude) threshold in `WhisperAsr` defaults to ~0.01, tuned for typical speaking levels (0.02–0.03)
  - The CLI flag `--silence-threshold` controls pause duration (in seconds) before sending the last heard phrase to the LLM, not the amplitude threshold
  - If detection is unreliable, verify your energy levels with the mic test and reduce background noise

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

1. **Test Microphone Only:**
   ```bash
   # Simple microphone level test
   python src/gaia/audio/tests/test_mic_simple.py
   ```

2. **Test Whisper Transcription:**
   ```bash
   # Basic 3-second recording and transcription loop
   python src/gaia/audio/tests/test_talk_basic.py
   ```

3. **Test Full Pipeline:**
   ```bash
   # Comprehensive test of all audio components
   python src/gaia/audio/tests/test_audio_pipeline.py
   ```

4. **Test Real-time Streaming:**
   ```bash
   # Test the WhisperAsr module directly with streaming
   python src/gaia/audio/whisper_asr.py --mode mic --stream --duration 20
   ```

## Audio System Architecture

```
Microphone → PyAudio → AudioRecorder → Voice Activity Detection → 
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
   pip show pyaudio  # Should be installed
   ```

2. **Check System Audio:**
   - Test microphone in another app (Voice Recorder, Discord, etc.)
   - Ensure no other app is using the microphone exclusively
   - Restart audio service: `net stop audiosrv && net start audiosrv` (Admin)

3. **File an Issue:**
   If problems persist, create an issue with:
   - Output of the microphone test
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
