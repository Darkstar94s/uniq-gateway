# UNIQ Hub - دليل تشغيل واختبار Matter Connector & Controller

يوثق هذا الدليل تشغيل النموذج الأولي (**UNIQ Hub Prototype**) القائم على **ThingsBoard IoT Gateway 3.8.4** و **Matter Controller (matterjs-server)** لدعم أجهزة **Matter over Wi-Fi** وأجهزة **Matter Bridges** (مثل SONOFF Zigbee Bridge و Tuya).

---

## 1. المعمارية المعتمدة (Architecture Overview)

```
 [أجهزة Matter Wi-Fi]         [جسور Matter Bridges (SONOFF)]
          │                                  │ (حساسات ومفاتيح فرعية)
          └─────────────────┬────────────────┘
                            │ بروتوكول Matter القياسي
                            ▼
           ┌────────────────────────────────┐
           │        matterjs-server         │  (Matter Controller)
           │  - Commissioning Manager       │
           │  - WebSocket Server (Port 5580)│
           └────────────────┬───────────────┘
                            │ WebSocket JSON-RPC (`ws://127.0.0.1:5580/ws`)
                            ▼
           ┌──────────────────────────────────────────────────┐
           │ UNIQ Matter Connector                            │
           │ (thingsboard_gateway/extensions/uniq/matter/)    │
           │  - Auto-reconnect WebSocket Client               │
           │  - Topology Parser (Isolates Bridged Endpoints)  │
           │  - Cluster-to-Telemetry Engine                   │
           │  - Bidirectional RPC (TB RPC ➔ Matter Command)   │
           │  - Local Commissioning API (POST :8282/matter/..)│
           └────────────────┬─────────────────────────────────┘
                            │ Python API (`send_to_storage`)
                            ▼
           ┌────────────────────────────────┐
           │    ThingsBoard IoT Gateway     │
           │  - Local File Storage Buffer   │ (يحفظ القراءات محلياً عند انقطاع النت)
           └────────────────┬───────────────┘
                            │ MQTT Gateway API (`v1/gateway/*`)
                            ▼
           ┌────────────────────────────────┐
           │    ThingsBoard Server/Cloud    │
           └────────────────────────────────┘
```

---

## 2. كيفية فصل وتمثيل جسور Matter (SONOFF Bridge Separation)

تتميز إضافتنا البرمجية بأنها **لا تدمج الجسر كجهاز واحد**، بل تقوم بفرز الـ Endpoints التابعة له:
* **جذر الجسر (Endpoint 0)**: يسجل كـ `Matter Bridge - SONOFF Zigbee Matter Bridge Pro ({NodeID})`.
* **الأجهزة التابعة للجسر (Endpoints 1..N)**:
  * تُقرأ سمات `nodeLabel` و `deviceType` لكل Endpoint.
  * يظهر كل حساس ومفتاح كجهاز مستقل في ThingsBoard:
    * `Bridged - SONOFF Living Room Light Relay (N{NodeID}-EP1)`
    * `Bridged - SONOFF Bedroom Temperature (N{NodeID}-EP2)`
    * `Bridged - SONOFF Front Door Contact (N{NodeID}-EP3)`
* **ثبات المعرفات**: يتم حفظ الربط في ملف محلي دائم `matter_device_registry.json` لضمان عدم تغير أسماء الأجهزة أو تكرارها عند إعادة تشغيل الـ Hub.

---

## 3. إعداد وتشغيل المنظومة (Running the Hub)

### المتطلبات:
* Python 3.9+
* Node.js 18+

### 1. تشغيل خادم Matter Controller (matterjs-server):
```bash
# تشغيل الخادم على المنفذ 5580
node thingsboard_gateway/extensions/uniq/matter/mock_matter_server.js
# أو تشغيل matterjs-server الرسمي بنفس إعدادات الـ WebSocket
```

### 2. تشغيل البوابة (ThingsBoard Gateway):
```bash
python thingsboard_gateway/tb_gateway.py -c thingsboard_gateway/extensions/uniq/config/tb_gateway_uniq.json
```

---

## 4. إضافة الأجهزة والاقتران (Commissioning API)

يوفر الـ Hub واجهة برمجة تطبيقات محلية خفيفة على المنفذ `8282` مخصصة لتطبيق الجوال:

### فحص حالة الخدمة:
```http
GET http://127.0.0.1:8282/matter/status
```
**الرد:**
```json
{
  "status": "online",
  "matterjs_connected": true,
  "matterjs_server_url": "ws://127.0.0.1:5580/ws",
  "commissioned_devices_count": 5,
  "hub": "UNIQ Smart Home Hub"
}
```

### إقران جهاز جديد بالـ QR Code أو Setup Code:
```http
POST http://127.0.0.1:8282/matter/commission
Content-Type: application/json

{
  "code": "MT:Y.SONOFF-PLUG-12345",
  "network_only": true,
  "wifi_ssid": "HomeNetwork",
  "wifi_password": "MySecretPassword"
}
```
**الرد:**
```json
{
  "status": "success",
  "node_id": 3,
  "message": "Device successfully commissioned on Matter Fabric with Node ID 3."
}
```
بمجرد نجاح الـ Commissioning، يقوم الـ Connector بمزامنة الجهاز الجديد فوراً وتسجيله وتمرير قياساته وسماته إلى سحابة ThingsBoard!

---

## 5. التحكم العكسي (ThingsBoard RPC ➔ Matter Devices)

عند إرسال أمر تحكم من شاشات لوحة التحكم بسحابة ThingsBoard:
* **طريقة التشغيل/الإطفاء (`setState`)**:
  ```json
  { "method": "setState", "params": true }
  ```
  يقوم الموصل بترجمتها إلى Matter Cluster `0x0006 (OnOff)` واستدعاء أمر `On` أو `Off` المقابل على رقم الـ Node والـ Endpoint المحدد للجهاز.
* **طريقة الإعتام والسطوع (`setBrightness`)**:
  ```json
  { "method": "setBrightness", "params": 75 }
  ```
  تترجم إلى أمر `MoveToLevel` في Cluster `0x0008 (LevelControl)`.
* **طريقة فتح/قفل الأبواب (`setLock`)**:
  ```json
  { "method": "setLock", "params": true }
  ```
  تترجم إلى أمر `LockDoor` أو `UnlockDoor` في Cluster `0x0101 (DoorLock)`.

---

## 6. التعامل مع انقطاع الإنترنت (Offline Buffering)

* تم ضبط البوابة على استخدام `storage: { type: "file" }`.
* في حال انقطع اتصال الإنترنت في المنزل بين الـ Hub والسحابة:
  1. يظل الـ Matter Controller والتحكم المحلي عبر الشبكة الداخلية يعملان بكفاءة 100%.
  2. يقوم الموصل بتمرير قراءات الحساسات عبر `send_to_storage`، فتقوم البوابة بحفظها محلياً في ملفات تخزين مؤقت مع أوقاتها الدقيقة (`ts`).
  3. فور عودة اتصال الإنترنت، تُفرغ البوابة كافة الحساسات المتراكمة تلقائياً إلى السحابة دون أي فقدان للبيانات.

---

## 7. كيفية إجراء الاختبار الشامل والتشخيص (Testing & Diagnostic Logs)

لتشغيل فحص الـ 7 مراحل الآلي الشامل للتأكد من عمل كافة أجزاء السلسلة:
```bash
python thingsboard_gateway/extensions/uniq/matter/test_matter_integration.py
```

### سجلات التشخيص المهمة (Logs):
* **سجلات موصل Matter**: ابحث في اللوج عن بادئة `[UniqMatterConnector]` أو `[UniqMatterClient]`.
* **سجلات تسجيل الأجهزة**: ابحث عن `Registered ThingsBoard Device: [Bridged - ...]`.
* **سجلات الـ RPC**: ابحث عن `Executing RPC for Matter device [...]`.
* **سجلات الـ Commissioning**: ابحث عن `Received commissioning request for code [...]`.
