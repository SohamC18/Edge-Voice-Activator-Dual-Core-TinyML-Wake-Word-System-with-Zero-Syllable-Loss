import tensorflow as tf
import re

# 1. Use the absolute path to bypass terminal folder issues
file_path = r"d:\work\172 proj\ai model\kws_dscnn_model.cc"

with open(file_path, "r") as f:
    cc_content = f.read()

# 2. Extract all the hexadecimal bytes using regex
hex_values = re.findall(r'0x[0-9a-fA-F]{2}', cc_content)
tflite_bytes = bytes([int(x, 16) for x in hex_values])

# 3. Load it into the TFLite Interpreter directly from memory
interpreter = tf.lite.Interpreter(model_content=tflite_bytes)
interpreter.allocate_tensors()

# 4. Extract the exact quantization parameters
in_det = interpreter.get_input_details()[0]
out_det = interpreter.get_output_details()[0]

print("--- C++ DEPLOYMENT PARAMS ---")
print(f"Input Scale: {in_det['quantization'][0]}")
print(f"Input Zero Point: {in_det['quantization'][1]}")
print(f"Output Scale: {out_det['quantization'][0]}")
print(f"Output Zero Point: {out_det['quantization'][1]}")