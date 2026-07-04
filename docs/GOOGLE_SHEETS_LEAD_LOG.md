# Google Sheets Lead Log

A live lead record for leadership visibility while the full HubSpot integration
is pending. The backend upserts one row per chat session (identified,
consenting leads only) into a Google Sheet via an Apps Script web app.
Bucket distribution, lead trends, and top use cases come from native Sheets
pivot tables/charts — no custom UI to build or host.

## How it works

```
chat turn → post-response enrichment (background thread)
         → persist_qualification()
         → app/integrations/google_sheets.py POSTs {action: "upsert_lead", row: {...}}
         → Apps Script web app upserts by session_id
```

- Rows are only written when the lead has an **email** and gave **lead_consent**.
- Repeat turns re-send the row; the script updates it in place, so scores stay
  current as the conversation progresses.
- Not configured (`GOOGLE_SHEETS_WEBHOOK_URL` unset) → silently disabled.

## Setup (one-time, ~10 minutes)

### 1. Create the sheet

Create a Google Sheet named e.g. **Mobcoder AI Lead Log** with a first sheet
named `Leads` and this exact header row (row 1):

```
logged_at | session_id | name | email | company | role | industry | project_type | project_need | timeline | budget_band | lead_bucket | score_total | score_fit | score_intent | score_value | cta_type | needs_human_review | page_url | score_reasons
```

(Column order must match `LEAD_ROW_FIELDS` in `app/integrations/google_sheets.py`.)

### 2. Add the Apps Script

Extensions → Apps Script, replace the default code with:

```javascript
const SHEET_NAME = "Leads";
const FIELDS = [
  "logged_at","session_id","name","email","company","role","industry",
  "project_type","project_need","timeline","budget_band","lead_bucket",
  "score_total","score_fit","score_intent","score_value","cta_type",
  "needs_human_review","page_url","score_reasons"
];
const SESSION_COL = FIELDS.indexOf("session_id") + 1;

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000); // serialize concurrent upserts
  try {
    const body = JSON.parse(e.postData.contents || "{}");
    if (body.action !== "upsert_lead" || !body.row || !body.row.session_id) {
      return _json({ ok: false, error: "bad request" });
    }
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
    const values = FIELDS.map(f => {
      const v = body.row[f];
      return v === undefined || v === null ? "" : v;
    });
    const last = sheet.getLastRow();
    if (last > 1) {
      const ids = sheet.getRange(2, SESSION_COL, last - 1, 1).getValues();
      for (let i = 0; i < ids.length; i++) {
        if (ids[i][0] === body.row.session_id) {
          sheet.getRange(i + 2, 1, 1, FIELDS.length).setValues([values]);
          return _json({ ok: true, updated: true });
        }
      }
    }
    sheet.appendRow(values);
    return _json({ ok: true, created: true });
  } finally {
    lock.releaseLock();
  }
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
```

### 3. Deploy as web app

Deploy → New deployment → type **Web app**:

- Execute as: **Me**
- Who has access: **Anyone** (the URL is an unguessable secret, same trust
  model as an incoming webhook; the script only accepts the upsert payload)

Copy the web app URL (`https://script.google.com/macros/s/…/exec`).

### 4. Configure the backend

```
GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/…/exec
```

Add it to the environment locally and to Terraform secrets for staging/prod.

## Suggested dashboard tabs (pivot tables)

- **Bucket distribution** — pivot on `lead_bucket`, count of rows (pie chart)
- **Leads over time** — pivot on `logged_at` (grouped by week), count (line chart)
- **Top use cases** — pivot on `project_type`, count
- **Hot leads** — filter view: `lead_bucket = hot`, sorted by `logged_at` desc
- **Review queue** — filter view: `needs_human_review = TRUE`
