import pyaudio
import serial
import numpy as np

PORT = 'COM4' # Verify this matches your ESP32
BAUD_RATE = 921600
CHUNK_SIZE = 320
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000

try:
    esp32_serial = serial.Serial(PORT, BAUD_RATE, timeout=1)
    print(f"Connected to ESP32 on {PORT} at {BAUD_RATE} baud.")
except Exception as e:
    print(f"Failed to connect: {e}")
    exit()

p = pyaudio.PyAudio()
stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK_SIZE)

print("Listening... Tap your laptop microphone to test.")

try:
    while True:
        audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        
        # Calculate volume on the laptop side for debugging
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_np.astype(np.float32)**2))
        
        # Print the volume to the terminal (overwrites the same line)
        print(f"Laptop Mic Volume (RMS): {rms:06.1f} | ESP32 Threshold: 30", end='\r')
        
        esp32_serial.write(audio_data)
        
except KeyboardInterrupt:
    print("\nStopping stream.")
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()
    esp32_serial.close()