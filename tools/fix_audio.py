import wave
import array

with open("trigger_audio.raw", "rb") as raw_file:
    pcm_data = raw_file.read()

# Load the raw bytes into an array of 16-bit signed integers ('h')
audio_array = array.array('h', pcm_data)

# Swap the High and Low bytes to reverse the serial shift
audio_array.byteswap()

# Write it out as a new WAV file
with wave.open("fixed_audio.wav", "wb") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(16000)
    wav_file.writeframes(audio_array.tobytes())

print("Bytes swapped! Saved as fixed_audio.wav. Go listen to it.")