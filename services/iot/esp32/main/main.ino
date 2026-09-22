#include <WiFi.h>

#include "ota.h"

#ifndef RGB_BUILTIN
#define RGB_BUILTIN 48
#endif

// Dynamic Wi-Fi & Server config variables loaded from LittleFS config.json on boot
String wifi_ssid;
String wifi_password;
String eap_identity;
String eap_username;
bool use_enterprise = false;
extern String server_url;
const String check_path = "/api/check";

void setup() {
    Serial.begin(115200);

    pinMode(LED_BUILTIN, OUTPUT);  // LED, for test ota

    // Initialize Filesystem first
    if (!initFS()) {
        Serial.println("Critical error: LittleFS initialization failed. Rebooting...");
        delay(1000);
        ESP.restart();
    }

    // Load configs dynamically
    if (!loadConfig(wifi_ssid, wifi_password, eap_identity, eap_username, use_enterprise,
                    server_url)) {
        Serial.println("[Config] Critical error: /config.json is missing or invalid! Halting...");
        while (true) {
            delay(1000);
        }
    }

    initOTA(server_url, check_path);
    listDir(LittleFS, "/", 1);

    // WiFi Connection
    bool connected = false;
    if (use_enterprise) {
        connected = initWiFiEnterprise(wifi_ssid, eap_identity, eap_username, wifi_password);
    } else {
        connected = initWiFi(wifi_ssid, wifi_password);
    }

    if (!connected) {
        ESP.restart();
    }

    // SNTP needs the network, so this runs only after WiFi connects, and
    // before markFirmwareValid()/loop() so every TLS handshake sees a real clock.
    if (!syncTimeSNTP()) {
        ESP.restart();
    }

    markFirmwareValid();
}

void loop() {
    // Show this build's colour first, so it is visible before any OTA kicks in.
    // The colour is the only per-version difference, so it is what tells you
    // by eye which build a device came back on after an update.
    Serial.println("LED: PURPLE (running v" FIRMWARE_VERSION ")");
    neopixelWrite(RGB_BUILTIN, 48, 0, 64);
    delay(POLL_INTERVAL_SECONDS * 1000);  // hold the colour, then re-check for an update

    // If wifi connected then check the latest firmware
    if (WiFi.status() == WL_CONNECTED) {
        // If the version greater than esp32 version then ota
        if (check()) {
            // A failed update is not a reason to reboot. The device keeps
            // running the image it has, check() stops offering a version that
            // cannot succeed, and the unchanged version it reports on the next
            // check-in is what surfaces the problem on the dashboard.
            if (downloadFirmwareToFS()) {
                OTA();  // Verifies the signature, flashes, and reboots into the new build
            } else {
                noteUpdateFailed("download");
            }
        }
    } else {
        // If cannot reconnect then restart esp32
        bool reconnected = false;
        if (use_enterprise) {
            reconnected = initWiFiEnterprise(wifi_ssid, eap_identity, eap_username, wifi_password);
        } else {
            reconnected = initWiFi(wifi_ssid, wifi_password);
        }
        if (!reconnected) {
            ESP.restart();
        }
    }
}
