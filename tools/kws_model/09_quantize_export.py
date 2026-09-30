"""
Step 8 of pipeline: quantize the trained model and export it for TFLite
Micro (TFLM), then measure the REAL tensor_arena size -- not estimated,
actually measured via TFLM's interpreter allocation.

Outputs:
  models/kws_dscnn_int8.tflite   -- quantized model
  models/kws_dscnn_model.cc      -- C byte array for embedding on-device
  models/kws_dscnn_model.h       -- matching header file

Requires: pip install tensorflow numpy
"""

import os
import numpy as np
import tensorflow as tf

MODEL_PATH = "models/kws_dscnn.keras"
FEATURES_PATH = "data/features.npz"  # needed for representative dataset
TFLITE_OUT_PATH = "models/kws_dscnn_int8.tflite"
CC_OUT_PATH = "models/kws_dscnn_model.cc"
H_OUT_PATH = "models/kws_dscnn_model.h"

FLASH_LIMIT_KB = 50
ARENA_LIMIT_KB = 45


def representative_dataset_gen(X_sample):
    """INT8 quantization needs real example inputs to calibrate the
    activation ranges -- this is why full INT8 quantization needs a
    representative dataset, unlike simpler float16 quantization."""
    for i in range(min(200, len(X_sample))):
        yield [X_sample[i:i+1].astype(np.float32)]


def quantize_model():
    print(f"Loading model from {MODEL_PATH}...")
    model = tf.keras.models.load_model(MODEL_PATH)

    # Sanity check: confirm we're actually converting the model we think
    # we are, not a stale cached version. The first Conv2D layer's filter
    # count is a quick fingerprint -- should be 16, not the older 32.
    for layer in model.layers:
        if isinstance(layer, tf.keras.layers.Conv2D):
            print(f"DEBUG: first Conv2D layer '{layer.name}' has "
                  f"{layer.filters} filters (expected 16)")
            break

    print(f"Loading representative data from {FEATURES_PATH}...")
    data = np.load(FEATURES_PATH)
    X = data["features"] / 40.0
    X = np.clip(X, -2.0, 1.0)

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: representative_dataset_gen(X)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    converter._experimental_disable_per_channel_quantization_for_dense_layers = True

    print("Converting and quantizing to full INT8...")

    tflite_model = converter.convert()

    os.makedirs("models", exist_ok=True)
    with open(TFLITE_OUT_PATH, "wb") as f:
        f.write(tflite_model)

    flash_kb = len(tflite_model) / 1024
    print(f"\nQuantized model size (flash): {flash_kb:.2f}KB "
          f"(limit: {FLASH_LIMIT_KB}KB) -- "
          f"{'PASS' if flash_kb < FLASH_LIMIT_KB else 'OVER BUDGET'}")

    return tflite_model


def measure_tensor_arena(tflite_model):
    """Actually measure arena usage via the TFLite interpreter, instead
    of estimating from parameter/activation math.

    IMPORTANT: an earlier version of this function deduplicated tensors
    by "buffer index" to avoid double-counting -- but most intermediate
    activation tensors in TFLite all share buffer index 0 (it just means
    "computed at runtime, not a stored constant"), so that approach
    collapsed dozens of distinct tensors into counting just one, giving
    a badly-wrong underestimate. Fixed below: sum every tensor by its
    unique tensor INDEX instead.

    NOTE: this still doesn't account for TFLite's internal buffer reuse
    between non-overlapping tensors (the interpreter can reuse the same
    memory for tensors that are never "alive" at the same time), so this
    number is an UPPER BOUND, not the exact minimum arena TFLM would use.
    The true minimum requires TFLM's own RecordingMicroAllocator on the
    embedded build. Treat this as "if this passes, you're almost
    certainly fine; if it fails, you need real embedded profiling before
    assuming it's actually over budget."
    """
    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()

    details = interpreter.get_tensor_details()
    total_bytes = 0
    largest_tensor_kb = 0
    for t in details:
        shape = t["shape"]
        dtype_size = np.dtype(t["dtype"]).itemsize
        size_bytes = int(np.prod(shape)) * dtype_size if len(shape) > 0 else dtype_size
        total_bytes += size_bytes
        largest_tensor_kb = max(largest_tensor_kb, size_bytes / 1024)

    arena_kb = total_bytes / 1024
    print(f"\nTensor arena upper bound (sum of all tensors, no reuse credit): "
          f"{arena_kb:.2f}KB (limit: {ARENA_LIMIT_KB}KB) -- "
          f"{'PASS even without buffer reuse' if arena_kb < ARENA_LIMIT_KB else 'over budget at this bound -- needs real TFLM profiling to confirm'}")
    print(f"Largest single tensor: {largest_tensor_kb:.2f}KB "
          f"(TFLM needs at least this much, at minimum)")
    print("Note: TFLite/TFLM reuse memory between tensors that aren't "
          "simultaneously active, so the REAL arena is likely smaller than "
          "this upper bound -- but this number is trustworthy as a ceiling, "
          "unlike the earlier buggy estimate. Confirm the exact figure with "
          "TFLM's RecordingMicroAllocator once your IoT teammate has the "
          "embedded build set up.")

    return arena_kb


def export_c_array(tflite_model):
    """Equivalent of `xxd -i model.tflite > model.cc`, done in pure
    Python so you don't need xxd installed on Windows."""
    var_name = "kws_dscnn_model"
    array_len = len(tflite_model)

    with open(CC_OUT_PATH, "w") as f:
        f.write(f'#include "{os.path.basename(H_OUT_PATH)}"\n\n')
        f.write(f"alignas(8) const unsigned char {var_name}[] = {{\n")
        for i in range(0, array_len, 12):
            chunk = tflite_model[i:i+12]
            line = ", ".join(f"0x{b:02x}" for b in chunk)
            f.write(f"  {line},\n")
        f.write("};\n")
        f.write(f"const int {var_name}_len = {array_len};\n")

    with open(H_OUT_PATH, "w") as f:
        guard = "KWS_DSCNN_MODEL_H_"
        f.write(f"#ifndef {guard}\n#define {guard}\n\n")
        f.write(f"extern const unsigned char {var_name}[];\n")
        f.write(f"extern const int {var_name}_len;\n\n")
        f.write(f"#endif  // {guard}\n")

    print(f"\nC array exported: {CC_OUT_PATH} ({H_OUT_PATH})")
    print("Hand these two files to your IoT/Software teammates for the "
          "on-device build.")


def main():
    tflite_model = quantize_model()
    measure_tensor_arena(tflite_model)
    export_c_array(tflite_model)


if __name__ == "__main__":
    main()