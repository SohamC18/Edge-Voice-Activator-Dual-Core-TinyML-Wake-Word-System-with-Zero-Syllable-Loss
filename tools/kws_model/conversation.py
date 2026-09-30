import wave

# Read the raw PCM data
with open("trigger_audio.raw", "rb") as raw_file:
    pcm_data = raw_file.read()

# Write it out as a standard playable WAV file
with wave.open("trigger_audio.wav", "wb") as wav_file:
    wav_file.setnchannels(1)      # Mono
    wav_file.setsampwidth(2)      # 16-bit (2 bytes per sample)
    wav_file.setframerate(16000)  # 16kHz sample rate
    wav_file.writeframes(pcm_data)

print("Audio saved as trigger_audio.wav! Go listen to it.")