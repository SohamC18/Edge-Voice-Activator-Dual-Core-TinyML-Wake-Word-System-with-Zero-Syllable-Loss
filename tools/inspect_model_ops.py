import tensorflow as tf
import re

file_path = r"d:\work\172 proj\ai model\kws_dscnn_model.cc"

with open(file_path, "r") as f:
    cc_content = f.read()

hex_values = re.findall(r'0x[0-9a-fA-F]{2}', cc_content)
tflite_bytes = bytes([int(x, 16) for x in hex_values])

interpreter = tf.lite.Interpreter(model_content=tflite_bytes)
interpreter.allocate_tensors()

# Extract unique operations
ops = set([op['op_name'] for op in interpreter._get_ops_details()])

print("--- REQUIRED C++ OPERATIONS ---")
for op in ops:
    print(op)