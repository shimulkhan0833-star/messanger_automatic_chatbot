# The Tech Rock Messenger chatbot

Python FastAPI webhook with LangChain + Groq replies. Existing `.env` secrets are preserved.

## Run locally (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/health to see missing configuration names. This endpoint does not test provider credentials.

## Configure `.env`

Keep your existing `GROQ_API_KEY`. Add the variables from `.env.example`:

- `META_VERIFY_TOKEN`: a random secret you choose; enter the same value in Meta's webhook setup.
- `META_APP_SECRET`: App Secret from your Meta app settings; used to authenticate incoming events.
- `META_PAGE_ACCESS_TOKEN`: Messenger access token for The Tech Rock Page.
- `META_PAGE_ID`: numeric ID of The Tech Rock Facebook Page.
- `META_GRAPH_VERSION`: supported Graph API version for your app (default v24.0).
- `GROQ_MODEL`: set explicitly to `openai/gpt-oss-20b`, the chat model tested with this project. The code's fallback is `llama-3.3-70b-versatile`, which was unavailable to this account. Prompt Guard models cannot generate these chatbot replies.

Restart the server after changing `.env`. Never commit or share secrets.

## MySQL and project files

Start your local MySQL server before starting FastAPI. Credentials are loaded from `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_DATABASE` in `.env`. SQLAlchemy 2 manages ORM queries, transactions, and connection pooling through the MySQL Connector driver with `use_pure=True`. Each storage operation uses its own session, so sessions are not shared between background threads. On startup, the app creates the configured database (default `tech_rock_chatbot`) and its `jobs` and `history` tables if absent. The configured user needs permission to create them. `create_all()` creates missing tables; it does not migrate existing schemas. Existing MySQL data is preserved. Future schema changes require a migration tool such as Alembic. Existing SQLite files are preserved but are no longer used; previous conversations are not automatically migrated.

- `main.py`: FastAPI routes and application lifecycle.
- `techrock/config.py`: shared environment loading from the project root.
- `tests/test_webhook.py`: webhook and worker tests.
- `techrock/db/database.py`: database/table creation, SQLAlchemy engine, session factory, and connection cleanup.
- `techrock/db/storage.py`: message queue and conversation storage operations.
- `techrock/db/models.py`: ORM models matching the existing MySQL tables.
- `techrock/services/chatbot.py`: LangChain + Groq reply generation.
- `techrock/services/messenger.py`: outgoing Meta API calls.
- `techrock/services/worker.py`: message processing and retries.

MySQL operations run in background threads to keep the async webhook responsive. The worker still supports only one process; MySQL alone does not make queue consumption safe across multiple workers.

## Connect Meta

1. Configure Messenger in your Meta developer app and connect The Tech Rock Page.
2. Expose port 8000 through a trusted HTTPS tunnel for local testing, or deploy to an HTTPS host.
3. Set callback URL to `https://YOUR-PUBLIC-HOST/webhook` and enter `META_VERIFY_TOKEN`.
4. Subscribe the Page to the app's `messages` webhook events. Ensure the access token has the required Messenger permissions.
5. Test with an account permitted by your app's development mode. Complete applicable app review/access requirements before public use.
6. Send a text message to the Page. The bot should reply automatically.

References: [Meta Messenger](https://developers.facebook.com/docs/messenger-platform/),
[Groq LangChain integration](https://console.groq.com/docs/langchain).

## Set up ngrok on Windows

Install ngrok in PowerShell, then reopen the terminal:

```powershell
winget install ngrok -s msstore
ngrok version
```

Create an account at [ngrok](https://dashboard.ngrok.com/signup). Copy your authtoken from the dashboard and configure it locally:

```powershell
ngrok config add-authtoken "YOUR_NGROK_AUTHTOKEN"
```

The ngrok authtoken is separate from Meta's verification token and Page access token. Follow the [official Windows setup guide](https://ngrok.com/download/windows) if installation fails.

With MySQL and FastAPI running, start a tunnel in another terminal:

```powershell
ngrok http 8000
```

Copy the HTTPS forwarding URL. In Meta's webhook settings, enter that URL followed by `/webhook` as the Callback URL, and the value of `META_VERIFY_TOKEN` from `.env` as the Verify token (without surrounding quotes). Leave client certificate attachment off, then click **Verify and save**. Ensure the Page is subscribed to `messages`.

## Stopping and restarting

Keep MySQL, FastAPI, and ngrok running while using the local chatbot.

- After changing `.env` or Python code, restart FastAPI to load the changes. Stop a foreground server with Ctrl+C, then run the startup command again. Changing `.env` alone does not update the running process.
- If ngrok is still running, restarting FastAPI on port 8000 does not require restarting ngrok.
- To restart ngrok, run `ngrok http 8000` again. If its public URL is unchanged, keep Meta's existing callback. If it changes, update Meta's Callback URL to the new HTTPS URL plus `/webhook` and click **Verify and save**.
- While ngrok is off, Meta cannot reach the local webhook. Messages already accepted into MySQL remain stored; new incoming messages cannot be received locally until the tunnel is available.
- Pending jobs resume after FastAPI restarts. Jobs already marked `failed` do not automatically retry.

During troubleshooting, a server was launched in the background with logs at `data/server-output.log` and `data/server-errors.log`. If that server is still running, stop that specific chatbot process before launching a replacement. Run only one chatbot server/worker; an address-in-use error means port 8000 is already occupied.

## If the bot does not reply

1. Open http://127.0.0.1:8000/health and fill any missing settings. A status of `ok` means settings are present, not that Meta/Groq credentials are valid.
2. Confirm ngrok forwards to port 8000 and Meta's callback matches its public URL with `/webhook`.
3. Check the server terminal or logs for `POST /webhook` with HTTP 200. If no request arrives, check the Page's `messages` subscription and the sending account's app testing access.
4. Verify `META_PAGE_ID` is The Tech Rock's numeric Page ID, not the Meta App ID. HTTP 403 on incoming events can indicate an incorrect `META_APP_SECRET`; HTTP 503 indicates missing webhook settings.
5. Inspect the MySQL queue with this query:

   ```sql
   SELECT id, status, attempts, reply IS NOT NULL AS has_reply
   FROM jobs ORDER BY id DESC LIMIT 10;
   ```

   No jobs means messages were not accepted for this Page. Pending/failed jobs without a saved reply suggest a failure before generation finishes. A saved reply with delivery failures suggests a Meta Send API or database completion problem. Earlier pending retries hold later messages for the same user to preserve order.
6. Keep `GROQ_MODEL=openai/gpt-oss-20b` unless you have verified another chat model is available to your key. Restart FastAPI after changing it. `model_not_found` means the selected model is unavailable; a Prompt Guard model is not suitable for chat replies.
7. If a timestamp `TypeError` appears, make sure the running server has loaded the current models: timestamp fields use `DOUBLE(asdecimal=False)` to return floats for the worker's age calculations.

Do not share access tokens, API keys, or App Secrets in screenshots or logs.

## Behavior and limits

- GET `/webhook` returns Meta's verification challenge. POST `/webhook` validates the raw-body SHA-256 signature.
- Verified text messages for the configured Page are saved in MySQL before acknowledgement; echo, delivery/read, and attachment-only events are ignored.
- A background worker replies and saves the last conversation turns for context. Failed jobs retry up to five times; only exception types are logged.
- Duplicate message IDs are ignored. A crash/network timeout after Meta accepts a reply but before recording success can still cause a duplicate outgoing reply.
- Jobs older than 23 hours expire to avoid sending delayed standard replies near the messaging-window boundary.
- Edit `knowledge.txt` with verified Page information. No live news/search or automatic post import is included. Bangla and English replies are supported by the prompt.
- Per-customer manual mode is persistent. Human requests pause replies silently; no admin notification service is included.
- Run **one server process/worker**, with a persistent MySQL database. Do not use multiple Uvicorn workers; the queue is designed for a single worker. Production scaling needs a shared queue and coordinated consumers.
- Conversation history and jobs are stored in the MySQL database `tech_rock_chatbot`. Add a retention/deletion policy before larger deployment. Failed jobs can be inspected in MySQL; there is no admin retry dashboard.

## Manual conversations

Enable `message_echoes` alongside `messages` in Meta's webhook subscriptions.
Send exactly `pause ai` from your Page inbox in the customer's conversation to
pause replies for that customer. Send `resume ai` when finished. Commands ignore
case and extra whitespace; customers can see them as ordinary messages.
Only Page-originated echoes without metadata and with no app_id or an explicitly
allowed inbox app_id are accepted as controls. META_MANUAL_REPLY_APP_IDS is a
comma-separated allowlist, defaulting to 263902037430900, the inbox app ID observed
on this Page's signed command echoes. Never add the chatbot's own Meta app ID.
Test the actual inbox you use, since other Page tools may use different app IDs.
See [Meta's sample echo handler](https://github.com/fbsamples/messenger-platform-samples/blob/main/node/app.js).

Pausing retires pending replies, including answers being generated. Resuming answers
new messages only. A send already in progress cannot be recalled. Duplicate commands
are ignored, and older commands cannot override newer ones. Modes survive restarts.
The server creates the new conversations and control_events tables on startup.

Explicit human requests in English or Bangla pause the conversation without a bot
answer. Other phrasing is classified by the AI using a structured handoff action;
ambiguous requests may be missed. No confirmation or separate admin alert is sent.
Use resume ai after handling the conversation. Customer messages during manual mode
are saved; ordinary replies typed by staff are not imported into AI history.

Restart the server, enable echo subscriptions, and test with an allowed Page test
account: pause, ask a question (silence), resume, ask a new question (reply), then
request a person (silence until resumed). Automated tests do not contact Meta or Groq.

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Tests use an in-memory storage double, fake credentials, and mocked AI/Send API calls; they never message your Page.
