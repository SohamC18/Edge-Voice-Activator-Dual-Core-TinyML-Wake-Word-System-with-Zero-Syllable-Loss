#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include "esp_task_wdt.h"

#include <tflm_esp32.h>       
#include <eloquent_tinyml.h>  
#include "kws_dscnn_model.h"  
#include "mel_filters.h"      
#include "arduinoFFT.h"       

#define LED_PIN 2
#define CHUNK_SAMPLES 320
#define THRESHOLD 300
#define AUDIO_BUFFER_SIZE 16000 

int16_t rolling_buffer[AUDIO_BUFFER_SIZE];
int16_t audio_buffer[CHUNK_SAMPLES];

// --- TENSORFLOW LITE CONFIGURATION ---
#define NUMBER_OF_INPUTS 4040 
#define NUMBER_OF_OUTPUTS 3   
#define TENSOR_ARENA_SIZE 30 * 1024 

Eloquent::TF::Sequential<10, TENSOR_ARENA_SIZE> ml;

const char* ssid = "SOHAMSLAPTOP";
const char* password = "Pass(0)18";
const char* server_ip = "192.168.137.1"; 
const uint16_t server_port = 8765;

WebSocketsClient webSocket;
int8_t ai_input_buffer[NUMBER_OF_INPUTS]; 

// --- DSP SCAFFOLDING (Hardware FPU Enabled) ---
float vReal[512];
float vImag[512];
ArduinoFFT<float> FFT = ArduinoFFT<float>(vReal, vImag, 512, 16000);

static float hann_window[512];
float* mel_db; 

unsigned long last_trigger_time = 0;
unsigned long led_turn_off_time = 0;
int new_samples_since_ai = 0;
bool loud_this_window = false; 

// --- FREERTOS SYNCHRONIZATION VARIABLES ---
TaskHandle_t AITaskHandle;
SemaphoreHandle_t ai_semaphore;
int16_t* ai_snapshot_buffer; 

volatile bool is_ai_busy = false;
volatile bool new_result_ready = false;
volatile float result_silence = 0.0;
volatile float result_unknown = 0.0;
volatile float result_friday = 0.0;


void init_dsp() {
  for (int i = 0; i < 512; i++) {
    hann_window[i] = 0.5f - 0.5f * cosf(2.0f * PI * i / 512.0f); 
  }
}

void compute_mel_spectrogram(int16_t* audio_in, int8_t* tensor_out) {
  const int N = 16000;
  float maxDb = -1e30f;

  for (int f = 0; f < 101; f++) {
    int start = f * 160 - 256; 
    
    for (int i = 0; i < 512; i++) {
      int idx = start + i;
      if (idx < 0) idx = -idx;                             
      if (idx >= N) idx = 2 * (N - 1) - idx;
      
      vReal[i] = (float)(audio_in[idx] / 32768.0f) * hann_window[i];
      vImag[i] = 0.0f;
    }
    
    FFT.compute(FFT_FORWARD);
    FFT.complexToMagnitude();

    for (int m = 0; m < 40; m++) {
      float e = 0;
      for (int k = 0; k <= 256; k++) {
        float p = vReal[k] * vReal[k]; 
        e += p * pgm_read_float(&mel_filters[m][k]);
      }
      float db = 10.0f * log10f(fmaxf(e, 1e-10f));
      mel_db[f * 40 + m] = db;
      if (db > maxDb) maxDb = db;
    }

    // Feed the watchdog every 16 frames to prevent panics
    if ((f & 0x0F) == 0) esp_task_wdt_reset();   
  }

  for (int i = 0; i < 101 * 40; i++) {
    float v = fmaxf(mel_db[i] - maxDb, -80.0f) / 40.0f;    
    int q = (int)roundf(v / 0.0078431377f) + 127;
    tensor_out[i] = (int8_t)constrain(q, -128, 127);
  }
}

// =========================================================
// FREERTOS CORE 0: DEDICATED AI COMPUTE TASK
// =========================================================
void ai_task_code(void * pvParameters) {
  esp_task_wdt_add(NULL); // Register task with Watchdog

  for(;;) {
    if(xSemaphoreTake(ai_semaphore, portMAX_DELAY) == pdTRUE) {
      
      compute_mel_spectrogram(ai_snapshot_buffer, ai_input_buffer);
      
      if (ml.predict(ai_input_buffer).isOk()) {
         float output_scale = 0.00390625; 
         int output_zero_point = -128;
         
         result_silence = (ml.outputs[0] - output_zero_point) * output_scale; 
         result_unknown = (ml.outputs[1] - output_zero_point) * output_scale; 
         result_friday  = (ml.outputs[2] - output_zero_point) * output_scale; 
         
         new_result_ready = true; 
      }
      
      esp_task_wdt_reset(); // Final feed after inference
      is_ai_busy = false; 
    }
  }
}

void setup() {
  Serial.setRxBufferSize(16384); 
  Serial.begin(921600);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  memset(rolling_buffer, 0, sizeof(rolling_buffer));

  // Safe Heap Allocations
  mel_db = new float[101 * 40];
  ai_snapshot_buffer = new int16_t[AUDIO_BUFFER_SIZE];
  
  if (!mel_db || !ai_snapshot_buffer) {
    Serial.println("FATAL: heap allocation failed");
    while (1) delay(1000);
  }
  
  init_dsp();

  ml.setNumInputs(NUMBER_OF_INPUTS);
  ml.setNumOutputs(NUMBER_OF_OUTPUTS);
  ml.resolver.AddFullyConnected();
  ml.resolver.AddShape();
  ml.resolver.AddStridedSlice();
  ml.resolver.AddDepthwiseConv2D();
  ml.resolver.AddMean();
  ml.resolver.AddMaxPool2D();
  ml.resolver.AddPack();
  ml.resolver.AddConv2D();
  ml.resolver.AddSoftmax();
  ml.resolver.AddReshape();

  Serial.println("Booting Neural Network...");
  while (!ml.begin(kws_dscnn_model).isOk()) {
    Serial.println(ml.exception.toString());
    delay(1000);
  }
  Serial.println("AI Model Loaded Successfully!");

  // Initialize FreeRTOS Task and Semaphore
  ai_semaphore = xSemaphoreCreateBinary();
  if (ai_semaphore == NULL) {
    Serial.println("FATAL: semaphore creation failed");
    while (1) delay(1000);
  }

  BaseType_t ok = xTaskCreatePinnedToCore(
      ai_task_code, "AI_Task", 16384, NULL, 1, &AITaskHandle, 0);
      
  if (ok != pdPASS) {
    Serial.println("FATAL: AI task creation failed");
    while (1) delay(1000);
  }

  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWi-Fi Connected!");

  webSocket.begin(server_ip, server_port, "/");
  webSocket.setReconnectInterval(5000);
}

// =========================================================
// FREERTOS CORE 1: I/O, AUDIO INGESTION, AND NETWORK TASK
// =========================================================
void loop() {
  webSocket.loop(); 

  if (new_result_ready) {
    new_result_ready = false; 

    if(webSocket.isConnected()) {
        char debug_msg[128];
        snprintf(debug_msg, sizeof(debug_msg), "[AI] Sil: %02.0f%% | Unk: %02.0f%% | Fri: %02.0f%%", 
                 result_silence * 100.0, result_unknown * 100.0, result_friday * 100.0);
        webSocket.sendTXT(debug_msg); 
    }

    if (result_friday > 0.50 && (millis() - last_trigger_time > 2000)) { 
        last_trigger_time = millis(); 
        digitalWrite(LED_PIN, HIGH); 
        led_turn_off_time = millis() + 1000; 
        
        if(webSocket.isConnected()) {
          webSocket.sendTXT("🔥 FRIDAY TRIGGERED!"); 
        }
    }
  }

  if (Serial.available() >= CHUNK_SAMPLES * 2) {
    
    while (Serial.available() >= CHUNK_SAMPLES * 2) {
      Serial.readBytes((char*)audio_buffer, CHUNK_SAMPLES * 2);

      memmove(rolling_buffer, rolling_buffer + CHUNK_SAMPLES, (AUDIO_BUFFER_SIZE - CHUNK_SAMPLES) * sizeof(int16_t));
      memcpy(rolling_buffer + (AUDIO_BUFFER_SIZE - CHUNK_SAMPLES), audio_buffer, CHUNK_SAMPLES * sizeof(int16_t));
      
      new_samples_since_ai += CHUNK_SAMPLES;
      
      float sum_squares = 0;
      for (int i = 0; i < CHUNK_SAMPLES; i++) {
        sum_squares += (audio_buffer[i] * audio_buffer[i]);
      }
      
      if (sqrt(sum_squares / CHUNK_SAMPLES) > THRESHOLD) {
        loud_this_window = true;
      }
    }

    if (new_samples_since_ai >= 9600) { 
      new_samples_since_ai = 0; 
      
      if (loud_this_window) {
        if (!is_ai_busy) {
          is_ai_busy = true; 
          memcpy(ai_snapshot_buffer, rolling_buffer, AUDIO_BUFFER_SIZE * sizeof(int16_t));
          xSemaphoreGive(ai_semaphore); 
        } else {
           if(webSocket.isConnected()) webSocket.sendTXT("[WARN] AI Core Busy, dropping frame");
        }
      } else {
         if(webSocket.isConnected()) webSocket.sendTXT("[IDLE] Audio too quiet (RMS < 300)");
      }
      
      loud_this_window = false; 
    }
  } 

  if (millis() > led_turn_off_time) {
      digitalWrite(LED_PIN, LOW);
  }
  
  yield(); 
}