# SaveBridge — שרת אישי

שרת חלופי לתוסף SaveBridge. מקבל את הבקשות מהתוסף, מריץ `yt-dlp` + `ffmpeg`,
ומחזיר את הקובץ — בתור MP4 רגיל, או כזרם **מוצפן** בפורמט המדויק שהתוסף יודע
לפענח לבד (`offscreen.js`). כך שום דבר חוץ מבתים אטומים לא עובר ברשת.

זהו קוד השרת בלבד. הוא תואם לתוסף הקיים ללא שינוי בפרוטוקול.

## מה השרת עושה

הוא מממש את ששת הנתיבים שהתוסף קורא אליהם:

| נתיב | תפקיד |
|---|---|
| `POST /api/hello` | רישום → מחזיר `token` |
| `GET /api/ping` | בדיקת חיים → גרסת yt-dlp ונוכחות ffmpeg |
| `POST /api/start` | פותח משימת הורדה → מחזיר `jobId` |
| `GET /api/jobs/{id}` | סטטוס: אחוז, מהירות, ETA, שם קובץ, מטא-דאטה של הצפנה |
| `POST /api/jobs/{id}/cancel` | ביטול |
| `GET /api/jobs/{id}/file` | הקובץ המוכן (רגיל או מוצפן) |

## הרצה מקומית (127.0.0.1)

הדרך הפשוטה והבטוחה ביותר — הכל בתוך המחשב, נטפרי לא מעורבת בהעברה כלל.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# צריך גם ffmpeg מותקן במערכת (apt/brew/מתקין ל-Windows)
uvicorn savebridge_server.server:app --host 127.0.0.1 --port 8723
```

בתוסף: הפנה את `SERVER_URL` אל `http://127.0.0.1:8723` (ראה "שינויים בתוסף").
במצב מקומי אין צורך בהצפנה — ההעברה כולה בתוך המחשב. אין צורך גם בפרוקסי:
ה-IP שלך כבר ביתי, לא דאטה-סנטר.

## פריסה על שרת מרוחק (Oracle Cloud Always Free)

1. צור VM חינמי (Ubuntu) ב-Oracle Cloud, ופתח בו פורטים 80/443 ב-Security List.
2. הצבע את הדומיין שלך (רשומת A) ל-IP הציבורי של ה-VM.
3. על ה-VM:
   ```bash
   git clone <this-repo> tosaf && cd tosaf
   bash deploy/setup-oracle.sh          # מתקין ffmpeg, venv, systemd
   ```
4. התקן Caddy, ערוך את `deploy/Caddyfile` עם הדומיין שלך, והרץ אותו —
   הוא מנפיק תעודת HTTPS אוטומטית מ-Let's Encrypt.
5. בתוסף: הפנה את `SERVER_URL` אל `https://הדומיין-שלך` והדלק הצפנה.

עדכון yt-dlp מדי פעם (יוטיוב משתנה): `./.venv/bin/pip install -U yt-dlp`.

## שינויים בתוסף

1. **`background.js`** — החלף את השורה:
   ```js
   var SERVER_URL = "https://extsync.com/sb-relay";
   ```
   בכתובת השרת שלך (`http://127.0.0.1:8723` מקומי, או `https://הדומיין-שלך`).

2. **`manifest.json`** — הוסף את הכתובת ל-`host_permissions`:
   ```json
   "host_permissions": [
     "https://*.youtube.com/*",
     "https://*.google.com/*",
     "http://127.0.0.1:8723/*",
     "https://הדומיין-שלך/*"
   ]
   ```

3. **הצפנה (מרוחק בלבד)** — הדלק את `encryptDownloads` בהגדרות התוסף.
   אם אין מתג בעמוד ההגדרות, אפשר להדליק ידנית מ-Console של התוסף:
   ```js
   chrome.storage.local.get("savebridge", (o) => {
     const s = o.savebridge || {};
     s.encryptDownloads = true;
     chrome.storage.local.set({ savebridge: s });
   });
   ```

## למה חובה פרוקסי, ואיך זה עובד

יוטיוב חוסמת שרתים לא רק לפי עוגיות — היא בודקת גם PO Token (Botguard), את
כתובת ה-IP (דאטה-סנטר מזוהה מיידית), ואת טביעת האצבע של חיבור ה-TLS. השרת
כאן מטפל בשלוש השכבות האלה:

| מנגנון הגנה | איך השרת מתמודד |
|---|---|
| טביעת אצבע TLS | `SAVEBRIDGE_IMPERSONATE=chrome` (ברירת מחדל) — משתמש ב-`curl_cffi` כדי שה-handshake ייראה בדיוק כמו Chrome אמיתי |
| PO Token / Botguard | סיידקאר `bgutil-provider` (Docker, מותקן ע"י `setup-oracle.sh`) שמנפיק טוקנים תקפים בזמן אמת; yt-dlp מוצא אותו לבד ב-`127.0.0.1:4416` |
| קליינט ה-API | `SAVEBRIDGE_PLAYER_CLIENTS=web,ios,android` (ברירת מחדל) — קליינטים של מובייל תלויים פחות ב-PO Token מלא מקליינט ה-web |
| IP דאטה-סנטר | **חובה פרוקסי** — גם עם כל השכבות למעלה, IP של Oracle/AWS/DigitalOcean מסומן כחשוד. הגדר `SAVEBRIDGE_PROXY_URL` לפרוקסי residential/mobile |

**הגדרת הפרוקסי** — ב-`/etc/systemd/system/savebridge.service` (או ב-`.env`
במצב מקומי), הוסף:

```
Environment=SAVEBRIDGE_PROXY_URL=http://user:pass@residential-proxy-host:port
```

תומך גם ב-`socks5://`. אחרי שינוי: `sudo systemctl restart savebridge`.
ספקי פרוקסי residential/mobile נפוצים: Webshare, IPRoyal, Bright Data,
Oxylabs — כל אחד עם תמחור שונה, לרוב לפי GB. פרוקסי datacenter רגיל (לא
residential) **לא יעזור** — הוא ייחסם באותה צורה.

אפשר לבדוק את התצורה בלי לחשוף את כתובת הפרוקסי עצמה:
```bash
curl http://127.0.0.1:8723/api/ping
# {"ytDlp":"...", "ffmpeg":true, "proxy":true, "impersonate":"chrome", "playerClients":["web","ios","android"]}
```

## נטפרי (רק אם בחרת בשרת מרוחק)

נטפרי מסננת לפי דומיין, אז דומיין חדש ייחסם עד שתבקש להוסיף אותו לרשימה הלבנה
של חשבונך: בצע הורדת ניסיון, שלח לנטפרי הקלטת תעבורה, ובקש להוסיף את הדומיין שלך.
במצב מקומי (127.0.0.1) אין צורך בזה כלל.

## אבטחה ופרטיות

- העוגיות שהתוסף שולח נכתבות לקובץ זמני, משמשות להורדה אחת בלבד, ונמחקות בסופה.
- הקובץ המוכן נמחק מהשרת מיד אחרי שנמשך אל המחשב (`jobs.cleanup`).
- ההצפנה: מפתח AES-256 אקראי לכל הורדה, עטוף ב-RSA-OAEP עם המפתח הציבורי של
  התוסף. רק המחשב שלך יכול לפענח.

## בדיקות

```bash
. .venv/bin/activate
python tests/test_crypto_matches_extension.py   # תואמות פורמט ההצפנה של התוסף
python tests/test_server_file_flow.py            # מחזור עבודה + הזרמת קובץ
python tests/test_ytdlp_opts.py                  # חיווט proxy/impersonate/player_clients
```
