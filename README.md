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
במצב מקומי אין צורך בהצפנה — ההעברה כולה בתוך המחשב.

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
```
