#include <Arduino.h>

#define LED_PIN 2 
#define CHUNK_SAMPLES 480 
#define THRESHOLD 300 // Setting it to 300 since your laptop output is ~7000

int16_t audio_buffer[CHUNK_SAMPLES];

void setup() {
  // CRITICAL FIX: Expand the hardware serial buffer to 1024 bytes
  Serial.setRxBufferSize(1024);
  
  Serial.begin(921600);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
}

void loop() {
  // Now the ESP32 has enough memory to actually hold all 960 bytes
  if (Serial.available() >= CHUNK_SAMPLES * 2) {
    Serial.readBytes((char*)audio_buffer, CHUNK_SAMPLES * 2);

    float sum_squares = 0;
    for (int i = 0; i < CHUNK_SAMPLES; i++) {
      sum_squares += (audio_buffer[i] * audio_buffer[i]);
    }
    float rms = sqrt(sum_squares / CHUNK_SAMPLES);

    if (rms > THRESHOLD) {
      digitalWrite(LED_PIN, HIGH);
    } else {
      digitalWrite(LED_PIN, LOW);
    }
  }
}