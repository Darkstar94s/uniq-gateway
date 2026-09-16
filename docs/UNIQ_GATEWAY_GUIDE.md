# دليل معمارية وتشغيل موزع UNIQ الذكي (UNIQ Gateway Architecture & Deployment Guide)

يعتمد **موزع UNIQ الذكي (UNIQ Hub)** على النواة الرسمية المستقرة لـ **ThingsBoard IoT Gateway**، مع إضافة طبقة موصلات وتراخيص مخصصة (**UNIQ Extension Overlay Layer**) دون لمس الكود الأصلي للبوابة لضمان سهولة الترقية والتحديث مستقبلاً.

---

## 1. الهيكل المعماري (Architecture)

```
                            سحابة UNIQ السحابية (UNIQ Cloud)
                                         ^
                                         | MQTT Gateway API (v1/gateway/*)
                                         v
+---------------------------------------------------------------------------------+
|                        موزع UNIQ الذكي (UNIQ Hub)                               |
|                                                                                 |
|   +-------------------------------------------------------------------------+   |
|   |                  ThingsBoard IoT Gateway Core (نواة البوابة الأصلية)    |   |
|   |   - إدارة الطوابير وتخزين البيانات محلياً عند انقطاع النت (File Storage)|   |
|   |   - إعادة الاتصال الذاتي وتشفير الاتصال والشهادات الأمنية (TLS/SSL)     |   |
|   |   - استلام أوامر الـ RPC والتحكم اللحظي وتمريرها للموصل المناسب         |   |
|   +------------------------------------+------------------------------------+   |
|                                        | Custom Connectors API                  |
|   +------------------------------------v------------------------------------+   |
|   |                 طبقة إضافات UNIQ (thingsboard_gateway/extensions/uniq/)  |   |
|   |                                                                         |   |
|   |   1. LicenseManager: محرك التراخيص السحابي التجاري (Lite / Pro / Ultra) |   |
|   |   2. UniqZigbeeConnector: موصل Zigbee 3.0 المباشر لحساسات ومفاتيح المنزل|   |
|   |   3. UniqMatterConnector: موصل Matter عبر الـ Thread والـ Wi-Fi         |   |
|   +-------------------------------------------------------------------------+   |
+---------------------------------------------------------------------------------+
```

---

## 2. إدارة وتحديث الكود من ThingsBoard الرسمي (Zero Conflicts Upstream Sync)

المستودع مهيأ برابطين:
- **`origin`**: مستودعك الخاص (`https://github.com/Darkstar94s/uniq-gateway.git`).
- **`upstream`**: المستودع الرسمي لـ ThingsBoard (`https://github.com/thingsboard/thingsboard-gateway.git`).

### كيف تقوم بتحديث كود ThingsBoard مستقبلاً دون التأثير على إضافات UNIQ؟
```bash
# 1. جلب التحديثات الرسمية من thingsboard
git fetch upstream

# 2. الانتقال لفرع العمل
git checkout bftech/uniq-gateway

# 3. دمج أحدث إصدار رسمي مع إضافاتنا
git merge upstream/master
# (أو دمج فرع إصدار محدد مثل upstream/release/3.8.4)

# 4. رفع النتيجة لحسابك على GitHub
git push origin bftech/uniq-gateway
```
بما أن كافة إضافات UNIQ موضوعة في مجلد مستقل `thingsboard_gateway/extensions/uniq/`، فإن الدمج يتم بسلاسة تامة دون أي تعارضات (Zero Merge Conflicts).

---

## 3. باقات وتراخيص البروتوكولات عن بُعد (Protocol Licensing)

يدعم موزع UNIQ ثلاث باقات تجارية:
1. **UNIQ Hub Lite**: يدعم `WiFi` + `Matter`.
2. **UNIQ Hub Pro**: يدعم `WiFi` + `Matter` + `Zigbee` + `BLE`.
3. **UNIQ Hub Ultra**: يدعم جميع البروتوكولات أعلاه + `Thread Border Router`.

### الترقية السحابية اللحظية (Hot-Reload):
يمكن لمدير النظام ترقية ترخيص أي موزع لدى العميل عن بُعد عبر تغيير سمات الجهاز (Shared Attributes) في سحابة UNIQ:
```json
{
  "licensedProtocols": ["matter", "zigbee", "ble"]
}
```
يلتقط الموزع التغيير فوراً ويقوم بتفعيل موصل Zigbee أو Matter دون الحاجة لإعادة تشغيل العتاد.

---

## 4. تشغيل الموزع (Running the Gateway)

### التشغيل المباشر عبر ملف إعدادات UNIQ:
```bash
python3 thingsboard_gateway/tb_gateway.py -c thingsboard_gateway/config/tb_gateway_uniq.json
```
