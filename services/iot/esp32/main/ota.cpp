#include "ota.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <NetworkClient.h>
#include <NetworkClientSecure.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <time.h>

// For RSA and SHA-256
// Docs: https://sourcevu.sysprogs.com/rp2040/lib/mbedtls/
#include <mbedtls/base64.h>
#include <mbedtls/md.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>

// What the server reads out of an uploaded image to learn what it is, so the
// model and version it stores cannot disagree with what the device will report.
// Both values are already in the binary as plain strings, but nothing tells
// them apart from the other version-shaped strings the core leaves there. The
// marker is what makes them findable, not what puts them there.
//
// Kept only because initOTA() prints it. `used` stops the compiler from
// dropping an unreferenced constant, but not the linker's --gc-sections, which
// removed this whole string when nothing read it. The printed line doubles as
// the boot breadcrumb saying what an image will publish as.
const char FIRMWARE_TAG[] __attribute__((used)) =
    "ESPOTA-BUILD{model=" DEVICE_MODEL ";version=" FIRMWARE_VERSION "}";

NetworkClientSecure* client = nullptr;
String server_url;
String check_path;
String download_path;
String version;
String signature;

// Offers already answered, cached so the server re-offering one costs no
// second download. Both are keyed on the signature rather than on the version
// string: the signature covers model|version|sha256, so it is bound to the
// bytes, while a version is a label an operator types and can republish over a
// corrected binary. Keyed on the label, that corrected binary would be
// suppressed as one of these until the device is power-cycled.
//
// RAM only, so a reboot re-learns both. State written at flash time would go
// stale the moment a new image failed its rollback check, whereas the running
// partition is always authoritative.
//
// An offer found to carry the image already running.
String skipped_signature;

// An offer whose update attempt did not complete, and how many attempts it has
// cost. A failed update is not a reason to reboot: the device keeps running
// the image it has. Without this it would re-download the same broken image
// every poll forever, so check() consults it to stop retrying one that cannot
// succeed. Counted per offer, so a newly published fix starts clean.
String failed_signature;
int failed_attempts = 0;

// Why the last attempt gave up, as a short stable token. Serial is the only
// other place this is said, and nobody is watching a serial port in the field,
// so it rides along with every check-in until a flash succeeds. A successful
// flash reboots, which clears this along with the rest of the RAM state, and
// that is exactly the moment it stops being true.
String last_error;

String rootCACertificate;
String rsaPublicKey;

// This unit's registration, issued by the dashboard and written into its own
// config.json. Per device rather than per account: config.json sits in
// LittleFS in the clear, so one secret shared across a fleet would mean buying
// the cheapest unit and dumping its flash yields the firmware for everything
// that account ships. The cost is that each unit needs its own LittleFS image.
//
// It identifies and never authorizes. The server accepts it for check-in and
// nothing else, so a secret pulled out of a teardown reads that unit's updates
// and cannot publish or withdraw anything.
String device_id;
String device_secret;

// Initialize and mount LittleFS
bool initFS() {
    if (!LittleFS.begin(true)) {
        Serial.println("LittleFS mount failed!");
        return false;
    }
    Serial.println("LittleFS mounted successfully!");
    return true;
}

// List all files and directories in a given path
void listDir(fs::FS& fs, const char* dirname, uint8_t levels) {
    Serial.printf("Listing directory: %s\r\n", dirname);

    File root = fs.open(dirname);
    if (!root) {
        Serial.println("- failed to open directory");
        return;
    }
    if (!root.isDirectory()) {
        Serial.println(" - not a directory");
        return;
    }

    File file = root.openNextFile();
    while (file) {
        if (file.isDirectory()) {
            Serial.print("  DIR : ");
            Serial.println(file.name());
            if (levels) {
                listDir(fs, file.path(), levels - 1);
            }
        } else {
            Serial.print("  FILE: ");
            Serial.print(file.name());
            Serial.print("\tSIZE: ");
            Serial.println(file.size());
        }
        file = root.openNextFile();
    }
}

// Calculate the SHA-256 hash of a file stored in LittleFS
String calculateFileSHA256(const char* path) {
    File file = LittleFS.open(path, "r");
    if (!file) return "";
    Serial.println("Load file and calculate sha256");

    // Init SHA-256 env
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);  // 0 for SHA-256, 1 for SHA-224

    // Read the file content in chunks and update the hash,
    // with a maximum of 1024 bytes each time
    uint8_t buf[1024];
    while (file.available()) {
        size_t len = file.read(buf, sizeof(buf));
        mbedtls_sha256_update(&ctx, buf, len);
    }
    file.close();

    // Free resources and get the final 32-byte hash.
    uint8_t hash[32];
    mbedtls_sha256_finish(&ctx, hash);
    mbedtls_sha256_free(&ctx);

    String hex = "";
    for (int i = 0; i < 32; i++) {
        char c[3];
        sprintf(c, "%02x", hash[i]);
        hex += c;
    }
    return hex;
}

// esp_image_header_t fields, mirrored from backend/domain/firmware_image.py.
static const size_t HASH_APPENDED_OFFSET = 23;
static const size_t IMAGE_DIGEST_BYTES = 32;

// Read the SHA-256 ESP-IDF appends to an application image: it occupies the
// trailing bytes, and the header flags whether it is present at all. Builds
// without it carry no digest to read.
bool readAppendedDigest(const char* path, uint8_t out[IMAGE_DIGEST_BYTES]) {
    File file = LittleFS.open(path, "r");
    if (!file) return false;

    uint8_t hashAppended = 0;
    if (!file.seek(HASH_APPENDED_OFFSET) || file.read(&hashAppended, 1) != 1 || hashAppended != 1) {
        file.close();
        return false;
    }

    size_t size = file.size();
    if (size < IMAGE_DIGEST_BYTES || !file.seek(size - IMAGE_DIGEST_BYTES) ||
        file.read(out, IMAGE_DIGEST_BYTES) != IMAGE_DIGEST_BYTES) {
        file.close();
        return false;
    }

    file.close();
    return true;
}

// Whether a downloaded image is byte-for-byte the running app. Compares the
// appended digest, which is what esp_partition_get_sha256() returns, not the
// manifest sha256, which covers the whole file and so never equals it.
//
// Docs:
// https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/storage/partition.html#_CPPv424esp_partition_get_sha256PK15esp_partition_tP7uint8_t
//
// Fails open: anything unreadable returns false and the image still flashes.
bool isImageAlreadyRunning(const char* path) {
    uint8_t candidate[IMAGE_DIGEST_BYTES];
    if (!readAppendedDigest(path, candidate)) return false;

    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return false;

    uint8_t current[IMAGE_DIGEST_BYTES];
    if (esp_partition_get_sha256(running, current) != ESP_OK) return false;

    return memcmp(candidate, current, sizeof(candidate)) == 0;
}

// Verify the RSA-PSS digital signature using the public key and manifest
bool verifyManifestSignature(const String& manifest, const String& b64Signature) {
    // Init public key container
    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);

    // Load public key
    if (mbedtls_pk_parse_public_key(&pk, (const unsigned char*)rsaPublicKey.c_str(),
                                    rsaPublicKey.length() + 1) != 0) {
        Serial.println("Public key parsing failed!");
        mbedtls_pk_free(&pk);
        return false;
    }

    // Base64 signature to string
    unsigned char sig[256];
    size_t sig_len = 0;
    int ret =
        mbedtls_base64_decode(sig, sizeof(sig), &sig_len,
                              (const unsigned char*)b64Signature.c_str(), b64Signature.length());
    if (ret != 0) {
        if (ret == MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL) {
            Serial.printf("Base64 decode failed: buffer too small, need %u bytes\n", sig_len);
        } else if (ret == MBEDTLS_ERR_BASE64_INVALID_CHARACTER) {
            Serial.println("Base64 decode failed: invalid character in signature");
        } else {
            Serial.printf("Base64 decode failed: error %d\n", ret);
        }
        mbedtls_pk_free(&pk);
        return false;
    }

    // Compute manifest string sha256
    unsigned char hash[32];
    mbedtls_md_context_t md_ctx;
    mbedtls_md_init(&md_ctx);
    mbedtls_md_setup(&md_ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0);
    mbedtls_md_starts(&md_ctx);
    mbedtls_md_update(&md_ctx, (const unsigned char*)manifest.c_str(), manifest.length());
    mbedtls_md_finish(&md_ctx, hash);
    mbedtls_md_free(&md_ctx);

    // Using padding.PSS mod
    mbedtls_rsa_context* rsa = mbedtls_pk_rsa(pk);
    mbedtls_rsa_set_padding(rsa, MBEDTLS_RSA_PKCS_V21, MBEDTLS_MD_SHA256);

    // Verify signature
    ret = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), sig, sig_len);
    mbedtls_pk_free(&pk);

    return (ret == 0);
}

// Initialize and configure the secure network client
void setClient() {
    client = new NetworkClientSecure();
    client->setCACert(rootCACertificate.c_str());
}

// Delete the network client and free resources
void delClient() {
    delete client;
    client = nullptr;
}

// Initialize OTA parameters with server URL and check path
bool initOTA(const String& serverUrl, const String& checkPath) {
    Serial.println(FIRMWARE_TAG);
    server_url = serverUrl;
    check_path = checkPath;
    return true;
}

// Connect to the specified WiFi network
bool initWiFi(const String& ssid, const String& password) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_STA);

    WiFi.begin(ssid, password);

    int count = 0;
    while (WiFi.status() != WL_CONNECTED && count < 10) {
        delay(1000);
        Serial.print(".");
        ++count;
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\nWiFi connect failed");
        return false;
    }
    Serial.println("\nWiFi connected");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("Gateway: ");
    Serial.println(WiFi.gatewayIP());
    Serial.print("RSSI: ");
    Serial.println(WiFi.RSSI());
    return true;
}

// Connect to WPA2 Enterprise WiFi network (e.g. PEAP)
bool initWiFiEnterprise(const String& ssid, const String& identity, const String& username,
                        const String& password) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_STA);

    WiFi.begin(ssid.c_str(), WPA2_AUTH_PEAP, identity.c_str(), username.c_str(), password.c_str());

    int count = 0;
    while (WiFi.status() != WL_CONNECTED && count < 20) {
        delay(1000);
        Serial.print(".");
        ++count;
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\nWiFi Enterprise connect failed");
        return false;
    }
    Serial.println("\nWiFi Enterprise connected");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("Gateway: ");
    Serial.println(WiFi.gatewayIP());
    Serial.print("RSSI: ");
    Serial.println(WiFi.RSSI());
    return true;
}

// Load OTA and Wi-Fi configurations from LittleFS JSON config
bool loadConfig(String& ssid, String& password, String& identity, String& username,
                bool& useEnterprise, String& serverUrl) {
    if (!LittleFS.exists("/config.json")) {
        Serial.println("Failed to find config file!");
        return false;
    }

    File file = LittleFS.open("/config.json", "r");
    if (!file) {
        Serial.println("Failed to open config file!");
        return false;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, file);
    file.close();

    if (error) {
        Serial.print("Failed to parse config file: ");
        Serial.println(error.c_str());
        return false;
    }

    ssid = doc["wifi_ssid"].as<String>();
    password = doc["wifi_password"].as<String>();
    identity = doc["eap_identity"].as<String>();
    username = doc["eap_username"].as<String>();
    useEnterprise = doc["use_enterprise"].as<bool>();
    serverUrl = doc["server_url"].as<String>();
    rootCACertificate = doc["ca_cert"].as<String>();
    rsaPublicKey = doc["public_key"].as<String>();
    device_id = doc["device_id"].as<String>();
    device_secret = doc["device_secret"].as<String>();

    if (ssid.isEmpty() || serverUrl.isEmpty() || rootCACertificate.isEmpty() ||
        rsaPublicKey.isEmpty() || device_id.isEmpty() || device_secret.isEmpty()) {
        Serial.println("Required config fields are missing or empty!");
        return false;
    }

    Serial.println("Config loaded successfully:");
    Serial.printf("SSID: %s\n", ssid.c_str());
    Serial.printf("Server URL: %s\n", serverUrl.c_str());
    // The id, never the secret. Serial output is the one place this is read
    // out loud, and printing the credential there is printing it into whatever
    // log the operator pasted the boot output into.
    Serial.printf("Device ID: %s\n", device_id.c_str());
    Serial.printf("WPA2 Enterprise: %s\n", useEnterprise ? "Yes" : "No");
    return true;
}

void noteUpdateFailed(const char* reason) {
    if (signature != failed_signature) {
        failed_signature = signature;
        failed_attempts = 0;
    }
    failed_attempts++;
    last_error = reason;
    Serial.printf("Update to %s failed: %s (%d attempt(s) so far).\n", version.c_str(), reason,
                  failed_attempts);
}

// Whether an offer that has already failed `attempts` times is worth another
// try on this check. Returning false makes check() ignore it until the server
// offers different bytes.
bool shouldRetryFailedOffer(int attempts) {
    if (attempts > 3) return false;

    return true;
}

// Check the server for an available firmware update
bool check() {
    Serial.println("Current version: " + String(FIRMWARE_VERSION));
    if (client == nullptr) setClient();

    // Everything the dashboard knows about a device arrives here. The check is
    // the only moment the device speaks, so anything the server wants to show
    // has to ride along with it.
    JsonDocument req;
    // From config, not derived from the hardware. The server issues the id at
    // registration, so it names a unit somebody registered rather than one
    // that merely exists on the network.
    req["device_id"] = device_id;
    req["device_secret"] = device_secret;
    req["model"] = DEVICE_MODEL;
    req["version"] = FIRMWARE_VERSION;
    req["poll_interval_seconds"] = POLL_INTERVAL_SECONDS;
    req["rssi"] = WiFi.RSSI();
    req["ip"] = WiFi.localIP().toString();
    if (!last_error.isEmpty()) {
        req["last_error"] = last_error;
        req["failed_attempts"] = failed_attempts;
    }
    String data;
    serializeJson(req, data);

    // Logged without the secret. Flash can be dumped by anyone holding the
    // board, so serial adds no capability to them, but a serial log is a thing
    // people paste into an issue and a flash dump is not.
    req["device_secret"] = "<redacted>";
    String loggable;
    serializeJson(req, loggable);
    Serial.println("Check request: " + loggable);

    HTTPClient https;
    https.begin(*client, server_url + check_path);
    https.addHeader("Content-Type", "application/json");

    int code = https.POST(data);
    if (code != HTTP_CODE_OK) {
        // 401 is its own line because it is the one failure the network cannot
        // cause. It means the server has no live registration for this id and
        // secret, which is a config.json problem: the unit was never
        // registered, was registered again, or has been disabled from the
        // dashboard. Nothing here retries out of it.
        if (code == HTTP_CODE_UNAUTHORIZED) {
            Serial.println(
                "Server does not recognise this device. Check device_id and "
                "device_secret in config.json, or whether it was disabled.");
        } else {
            Serial.println("http connect error: " + String(code));
        }
        https.end();
        delClient();
        return false;
    }

    String res = https.getString();
    https.end();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, res);
    if (err) {
        Serial.print("json deserialize error: ");
        Serial.println(err.c_str());
        delClient();
        return false;
    }

    if (doc["update_available"] != true) {
        Serial.println("no new version");
        delClient();
        return false;
    }

    version = doc["version"].as<String>();
    signature = doc["signature"].as<String>();
    download_path = doc["download_url"].as<String>();

    // Both answers below are about bytes this device has already seen, so they
    // are read off the signature. The version only names them in the log.
    if (!skipped_signature.isEmpty() && signature == skipped_signature) {
        Serial.println("Ignoring " + version + ": already known to be the running image");
        delClient();
        return false;
    }

    if (signature == failed_signature && !shouldRetryFailedOffer(failed_attempts)) {
        Serial.println("Ignoring " + version + " after " + String(failed_attempts) +
                       " failed attempt(s)");
        delClient();
        return false;
    }

    return true;
}

// Copy the offered image into LittleFS. Split out so downloadFirmwareToFS()
// can own the teardown once for every way this ends.
bool streamFirmwareToFS(HTTPClient& https) {
    int code = https.GET();
    if (code != HTTP_CODE_OK) {
        Serial.println("http connect error: " + String(code));
        return false;
    }

    File file = LittleFS.open("/firmware.bin", "w");
    if (!file) {
        Serial.println("Error: Failed to open /firmware.bin for writing.");
        return false;
    }

    int written = https.writeToStream(&file);
    file.close();
    if (written < 0) {
        // Covers a truncated body too: writeToStream compares Content-Length
        // against what it copied and fails rather than returning a short
        // count, so there is no partial success to test for here. The bytes
        // it did write are dropped, since a partial image is worth nothing
        // and this partition only holds one.
        Serial.printf("Firmware download failed: writeToStream error %d\n", written);
        return false;
    }

    return true;
}

// Download the firmware binary file to LittleFS
//
// The TLS client is released after the HTTPClient that borrowed it is gone,
// which is what the inner scope is for. ~HTTPClient runs end() again and
// touches the client it was handed, so freeing it first turns the return into
// a jump through a freed vtable: a 404 panicked the device at the closing
// brace with PC=0x0 until this was scoped. Only a failure reaches that, since
// end() on a fully read body drops the pointer and leaves nothing to touch.
//
// Every way out leaves the same state, rather than each path deciding for
// itself. setClient() only allocates when the pointer is null, so a client
// left behind is the one the next attempt runs on, session and all, and a
// partial image left behind is 1.1 MB of a 1.25 MB partition held until
// something happens to overwrite it.
bool downloadFirmwareToFS() {
    if (client == nullptr) setClient();

    bool downloaded = false;
    {
        HTTPClient https;
        https.begin(*client, server_url + download_path);
        downloaded = streamFirmwareToFS(https);
        https.end();
    }

    delClient();
    if (!downloaded) LittleFS.remove("/firmware.bin");
    return downloaded;
}

// Split up to 3 dot-separated integer segments into out[]. Returns the count
// actually found, so "1.2" yields 2 rather than being padded with a 0.
int parseVersionSegments(const String& v, int out[3]) {
    int count = 0;
    int start = 0;
    for (int part = 0; part < 3; part++) {
        int dot = (part < 2) ? v.indexOf('.', start) : -1;
        String segment = (dot == -1) ? v.substring(start) : v.substring(start, dot);
        if (segment.length() == 0) break;
        out[count++] = segment.toInt();
        if (dot == -1) break;
        start = dot + 1;
    }
    return count;
}

// Check if v1 is newer than v2. Returns true if v1 > v2.
//
// A version that is a strict prefix of another, e.g. "1.2" vs "1.2.0",
// compares as older rather than equal.
bool isVersionNewer(const String& v1, const String& v2) {
    int seg1[3];
    int seg2[3];
    int n1 = parseVersionSegments(v1, seg1);
    int n2 = parseVersionSegments(v2, seg2);

    int n = min(n1, n2);
    for (int i = 0; i < n; i++) {
        if (seg1[i] > seg2[i]) return true;
        if (seg1[i] < seg2[i]) return false;
    }
    return n1 > n2;
}

// Sync system time via SNTP. Needs an active WiFi connection, and must run
// before any TLS handshake, since NetworkClientSecure validates the server
// cert's notBefore/notAfter against the device clock.
bool syncTimeSNTP() {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 15000)) {
        Serial.println("SNTP time sync failed");
        return false;
    }

    Serial.print("SNTP time synced: ");
    Serial.println(asctime(&timeinfo));
    return true;
}

// Mark firmware valid to cancel auto-rollback
void markFirmwareValid() {
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err == ESP_OK) {
        Serial.println("Firmware marked as valid (rollback canceled).");
    } else {
        Serial.printf("Failed to mark firmware as valid, error: 0x%x\n", err);
    }
}

// Execute the OTA update process, including verification and flashing
//
// Returning at all means the update did not happen, since a flash that works
// reboots from inside here. Every path out leaves LittleFS without a staged
// image: the file is worth nothing once a reason to reject it is known, and
// the partition it sits in has room for barely more than one copy.
void OTA() {
    String fileSha256 = calculateFileSHA256("/firmware.bin");
    if (fileSha256.isEmpty()) {
        Serial.println("Error: Failed to open /firmware.bin for hashing.");
        LittleFS.remove("/firmware.bin");
        noteUpdateFailed("hash");
        return;
    }
    Serial.println("SHA-256: " + fileSha256);

    // Manifest String
    String manifest = String(DEVICE_MODEL) + "|" + version + "|" + fileSha256;
    Serial.println("Firmware metadata:" + manifest);

    // Compare RSA signature
    if (verifyManifestSignature(manifest, signature)) {
        Serial.println("Digital signature verification passed.");
    } else {
        Serial.println("Error: Digital signature verification failed.");
        LittleFS.remove("/firmware.bin");
        noteUpdateFailed("signature");
        return;
    }

    // Downgrade protection / Freshness check
    if (!isVersionNewer(version, FIRMWARE_VERSION)) {
        Serial.printf(
            "Error: Downgrade attack detected. Version %s is not newer than current %s.\n",
            version.c_str(), FIRMWARE_VERSION);
        LittleFS.remove("/firmware.bin");
        noteUpdateFailed("downgrade");
        return;
    }

    if (isImageAlreadyRunning("/firmware.bin")) {
        Serial.printf("Version %s carries the image already running; not flashing.\n",
                      version.c_str());
        skipped_signature = signature;
        LittleFS.remove("/firmware.bin");
        return;
    }

    // Writing firmware
    Serial.println("Writing to system partition...");
    File updateBin = LittleFS.open("/firmware.bin", "r");
    if (!updateBin) {
        Serial.println("Error: Failed to open /firmware.bin for flashing.");
        LittleFS.remove("/firmware.bin");
        noteUpdateFailed("open");
        return;
    }
    size_t updateSize = updateBin.size();

    if (Update.begin(updateSize)) {
        size_t written = Update.writeStream(updateBin);

        if (Update.end()) {
            if (Update.isFinished() && !Update.hasError()) {
                Serial.printf("Update Success! Written: %u bytes\n", written);
                updateBin.close();
                LittleFS.remove("/firmware.bin");
                delay(2000);
                ESP.restart();
            } else {
                Serial.printf("Update finished but has errors: %u\n", Update.getError());
                Serial.printf("Progress: %u / %u\n", Update.progress(), Update.size());
                updateBin.close();
                LittleFS.remove("/firmware.bin");
                noteUpdateFailed("write");
            }
        } else {
            Serial.printf("Update.end() failed: %s\n", Update.errorString());
            updateBin.close();
            LittleFS.remove("/firmware.bin");
            noteUpdateFailed("end");
        }
    } else {
        Serial.println("Not enough space to begin update");
        updateBin.close();
        LittleFS.remove("/firmware.bin");
        noteUpdateFailed("space");
    }
}
