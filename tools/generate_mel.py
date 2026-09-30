import librosa
import numpy as np

# Replicate the exact 16kHz, 512 FFT, 40-band Mel filterbank
mel_basis = librosa.filters.mel(sr=16000, n_fft=512, n_mels=40)

# Export as a C++ header file, explicitly forcing it into Flash memory
with open("mel_filters.h", "w") as f:
    f.write("#ifndef MEL_FILTERS_H\n#define MEL_FILTERS_H\n\n")
    f.write("#include <Arduino.h>\n\n")
    f.write("const float mel_filters[40][257] PROGMEM = {\n")
    for i in range(40):
        f.write("  {")
        f.write(", ".join([f"{val:.6f}" for val in mel_basis[i]]))
        f.write("},\n")
    f.write("};\n\n#endif\n")
print("Successfully generated mel_filters.h!")