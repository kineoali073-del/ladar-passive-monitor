# LADAR Passive ADS-B Monitor

واجهة محلية لعرض بيانات ADS-B التي يستقبلها `readsb` أو `dump1090` فعلياً. النظام استقبال سلبي فقط: لا يرسل RF ولا يشوّش ولا يتحكم بأجهزة أو أسلحة.

## التشغيل

```bash
python -m pip install -r requirements.txt
python run_ladar.py
```

الخادم محلياً على `127.0.0.1:8000`. من طرفية أخرى:

```bash
curl -fsS http://127.0.0.1:8080/data/aircraft.json
curl -fsS http://127.0.0.1:8000/api/status
curl -fsS 'http://127.0.0.1:8000/api/contacts?include_reference=true'
```

WebSocket: `/ws/contacts`; عقد الرسالة `type=contacts_snapshot`, `published_at_utc`, `source_connected`, `data.contacts`.

## إعدادات اختيارية

`READSB_FEED_URL`, `READSB_POLL_INTERVAL_SEC`, `LADAR_DB_PATH`, `LADAR_SENSOR_LAT`, `LADAR_SENSOR_LON`, `LADAR_RECEIVER_HEIGHT_M`, `LADAR_FRONT_END`, `LADAR_HOST`, `LADAR_PORT`. لا توجد إحداثيات مستقبل افتراضية؛ اضبط القياسات الفعلية فقط. عنوان الاستماع الافتراضي محلي `127.0.0.1`.

## حدود النظام

وقت القياس يشتق حصراً من `now - seen_pos`، ولا يحل `seen` أو وقت وصول HTTP محله. `priority_score` أولوية رصد عتبية، وليس احتما��اً أو حكماً بوجود تهديد. نقص القياسات أو موقع المستقبل المطلوب ينتج `UNVERIFIED`. الأفق الراديوي تقدير هندسي لا يثبت الكشف. ADS-B استقبال تعاوني وليس راداراً، ولا يكشف وحده أهدافاً لا تبث ADS-B. لا ينشئ التطبيق سجلات REFERENCE تلقائياً. بيانات الاختبار الاصطناعية محصورة في fixtures ولا تُستخدم وقت التشغيل.

## الاختبارات

```bash
python -m pytest -q tests/test_pipeline_contracts.py
```

## Termux

لا تستخدم `sudo` في Termux غير المروّت. قد لا تتوفر عجلات Python متوافقة مع جميع إصدارات Android؛ فشل تثبيت الاعتماديات يعني أن الخادم لم يبدأ، وتثبيت Rust وحده لا يضمن التوافق. أدوات النظام `build-essential` و`pkg-config` و`libusb-1.0-0-dev` ليست متطلبات عامة لهذا الخادم، بل لمكونات عتاد منفصلة عند الحاجة.
