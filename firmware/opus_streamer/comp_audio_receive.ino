#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <opus.h> // Include the Opus codec library

#define LED_PIN 2
#define CHUNK_SAMPLES 320 // Must be 320 to satisfy Opus 20ms requirement
#define THRESHOLD 300

// Wi-Fi and Server config
const char* ssid = "Xiaomi11i";
const char* password = "Pass(0)18";
const char* server_ip = "10.178.232.79"; // Your laptop's IPv4 address
const uint16_t server_port = 8765;

WebSocketsClient webSocket;
int16_t audio_buffer[CHUNK_SAMPLES];

// Opus Encoder Variables
OpusEncoder *encoder;
int opus_err;
#define MAX_PACKET_SIZE 1000 // Increased from 250 to prevent memory crashing
unsigned char opus_out_buffer[MAX_PACKET_SIZE];

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  if (type == WStype_CONNECTED) {
    Serial.println("WebSocket Connected!");
  }
}

void setup() {
  Serial.setRxBufferSize(1024);
  Serial.begin(921600);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Initialize the Opus Encoder (16kHz, Mono, VoIP mode)
  encoder = opus_encoder_create(16000, 1, OPUS_APPLICATION_VOIP, &opus_err);
  if (opus_err != OPUS_OK) {
    Serial.println("Failed to create Opus encoder!");
  } else {
    Serial.println("Opus Encoder Initialized Successfully.");
  }

  // Connect to Wi-Fi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  // Connect WebSocket
  webSocket.begin(server_ip, server_port, "/");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
}

void loop() {
  webSocket.loop(); 

  if (Serial.available() >= CHUNK_SAMPLES * 2) {
    Serial.readBytes((char*)audio_buffer, CHUNK_SAMPLES * 2);

    float sum_squares = 0;
    for (int i = 0; i < CHUNK_SAMPLES; i++) {
      sum_squares += (audio_buffer[i] * audio_buffer[i]);
    }
    float rms = sqrt(sum_squares / CHUNK_SAMPLES);

    if (rms > THRESHOLD) {
      digitalWrite(LED_PIN, HIGH);
      
      // 1. Compress the raw PCM audio using Opus
      int compressed_bytes = opus_encode(encoder, audio_buffer, CHUNK_SAMPLES, opus_out_buffer, MAX_PACKET_SIZE);
      
      // 2. Send the tiny compressed packet
      if (compressed_bytes > 0) {
        webSocket.sendBIN(opus_out_buffer, compressed_bytes);
      } else {
        // THIS IS NEW: Print the error code to the Serial Monitor
        Serial.print("Opus Encoding Failed! Error code: ");
        Serial.println(compressed_bytes);
      }
      
    } else {
      digitalWrite(LED_PIN, LOW);
    }
  }
}