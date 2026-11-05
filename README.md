![Gmail Assistant](https://media2.dev.to/dynamic/image/width=1000,height=420,fit=cover,gravity=auto,format=auto/https%3A%2F%2Fdev-to-uploads.s3.us-east-2.amazonaws.com%2Fuploads%2Farticles%2Fsbeuj41kjrl6cdsiabw3.png)

# Gmail Assistant

A personal email assistant you talk to in plain language through Telegram. It runs on Amazon Bedrock AgentCore Runtime with a Strands Agents SDK agent, and the whole stack is defined with AWS CDK (TypeScript).

## What it does

- **Talk to it in plain language**, in Italian or in English. The assistant uses its tools when it needs them. There are no slash commands to learn.
- **Daily digest at 10:00 (Europe/Rome):** reads unread inbox emails and sends a summary grouped into healthcare, invoices, payments, discounts and offers (each with a verdict: act or skip), other important mail, and everything else. You can also ask for it at any time, for example "give me the digest".
- **Reminders tied to an email:** you ask for a reminder about a specific email at a time you choose, or you confirm one of the reminder proposals that the digest found in bills and appointments. The reminder reads the email when it fires and always sends, with a note if you have already read it, because a bill can still need action after it was opened.
- **Mailbox:** list emails, read one, list folders, mark emails as read, move emails into a folder (created if missing), list an email's attachments and send one to the chat as a file, and undo the last change.
- **Memory:** it remembers your preferences, for example the offers you discard, and uses them in the digest. Nothing else is extracted, and the events of the chat expire after 7 days. You can ask what it remembers, and you can ask it to forget an item. Code requires your request and a clear yes in a later message.
- **Unsubscribe options:** it shows you the unsubscribe link or address of a newsletter. It never clicks or sends anything for you.
- **Formatting:** the model's Markdown is converted to Telegram's HTML, and plain text is used if Telegram rejects it.
- **Safety:** email content is untrusted. The agent never sees the raw text of an email, it changes mail only when your message asks for it, and only your Telegram account is answered. See *Safety layers* below.

## Architecture

```
Telegram ──webhook──▶ Lambda: webhook ──async──▶ Lambda: worker ──InvokeAgentRuntime──▶ AgentCore Runtime
                      (secret token,             │      ▲                                 (Strands agent,
                       chat-ID allowlist)        │      │                                  Nova Pro on Bedrock)
                                                 │      │                                        │
EventBridge Scheduler (10:00 Europe/Rome) ───────┘      │                                        ▼
EventBridge Scheduler (one reminder per email) ─────────┘                                 Gmail API (OAuth)
                                                                                    AgentCore Memory
DynamoDB: action log (undo), language of the last chat message, reminder proposals, handled Telegram update ids (1-day TTL)
SSM Parameter Store (SecureString, /gmail-assistant/*): read by the Lambdas and the runtime
```

| Piece | Where | Notes |
|---|---|---|
| Strands agent + tools | `agent/` | Docker image, ARM64, served by `BedrockAgentCoreApp` |
| AgentCore Runtime | `infra/lib/gmail-assistant-stack.ts` | Name `gmail_assistant`, public network, HTTP protocol |
| Long-term memory | `agent/gmail_assistant/memory.py` | One strategy, preferences. Chat events are kept 7 days |
| Telegram webhook | `lambdas/webhook.py` | Lambda Function URL, rejects requests without the secret token and ignores an update id it has already handled. Reserved concurrency 5 |
| Chat worker | `lambdas/worker.py` | Calls the agent, sends the reply, and handles reminders and the digest schedule. No automatic retries. Reserved concurrency 1: one agent turn at a time, a second event waits up to 30 minutes |
| Daily digest | EventBridge Scheduler in the stack | `cron(0 10 * * ? *)` in `Europe/Rome`, bounded retries |
| Reminders | EventBridge Scheduler, created on demand | One-off schedules that delete themselves after they fire |
| DynamoDB | `ActionLog` table | Four kinds of items: action log, language of the last message, reminder proposals, handled update ids (expire after a day, attribute `expires`) |
| Secrets | SSM Parameter Store (SecureString) | Bot token, webhook secret, Gmail OAuth JSON. Created by scripts, deleted by `scripts/teardown.py` |

The model defaults to `eu.amazon.nova-pro-v1:0` (EU inference profile). Override it with `-c modelId=...`, using a model that is enabled for your account. You can check a model with `aws bedrock-runtime converse --region eu-west-1 --model-id <id> --messages '[{"role":"user","content":[{"text":"ok"}]}]'`.

## The tools

The agent decides when to call these tools. You never call them yourself.

| Tool | What it does |
|---|---|
| `list_emails`, `read_email` | Find and read emails. The chat agent gets a neutral title and a summary from the quarantined reader, not the raw text. Lists are read in blocks of ten, in parallel (50 emails took 5.6 s in one measurement). The digest agent gets the raw text through read-only versions of the same tools |
| `run_digest` | Prepares the digest of unread emails, with reminder proposals |
| `mark_as_read`, `move_to_folder`, `star_emails`, `trash_emails` | Change mail. Checked by code: your message has to ask for it, the emails have to be ones you have seen, and the Trash needs a confirmation with a code |
| `undo_last_action` | Reverses the most recent mark, star, move or trash, after a yes. Only what the action changed is put back |
| `list_attachments`, `send_attachment` | Send an attachment to the chat. Sending is checked like a change: your message has to ask for a file. File names are screened |
| `unsubscribe_info` | Reads the unsubscribe option of a newsletter (at most two, screened). Never clicks it |
| `set_email_reminder` | Creates a reminder about one email at a local time |
| `list_reminder_proposals` | Lists the proposals found in the last digest |
| `list_reminders`, `cancel_reminder` | Shows and cancels scheduled reminders |
| `list_memories`, `forget_memory` | Shows what is remembered, and deletes one item after a confirmation with a code |
| `list_folders` | Lists your Gmail folders |

## Safety layers

An email can contain text that tries to give orders to an AI assistant. The prompt tells the model to ignore it, but a prompt is a request, so most of the protection is code.

| Layer | What it does | Where |
|---|---|---|
| Agents with different powers | The digest and the reminders use two read tools and nothing that changes mail | `agent/gmail_assistant/agent.py`, `readonly_tools.py` |
| Quarantined reader | The chat agent sees a neutral title, a category and a summary produced by a model call without tools, with links removed. Never the raw subject or text. The sender is the address in angle brackets, and the date is parsed, not passed as text | `reader.py`, `tools.py` |
| Bedrock Guardrails | The text of each email is screened with `ApplyGuardrail` (prompt-attack filter). A hit hides the text and marks the email suspicious. The text of the digest is screened too before the chat agent sees it, and reminder proposals are validated (id, time) and their subjects screened | `screen.py`, `infra/lib/gmail-assistant-stack.ts` |
| Email data is not stored | Before a message is written to AgentCore Memory, the results of every tool that returns text from an email (lists, reads, attachments, reminders, proposals, memories), and the answers that show email data, are replaced by a note | `storage.py` |
| No stale lists | Before each turn, email data older than 10 minutes, and answers that show email data (an address, or a title or summary returned by a read tool) without a tool call, are removed from the context of the model | `history.py` |
| The user has to ask | A tool that changes mail, sets a reminder or sends an attachment runs only if your message in that turn asks for it. The patterns match requests, not ordinary words ("marketing", "archivio", "annullato") | `guard.py` |
| Only emails you have seen | A change is refused for any email that was not listed in the conversation, and at most 10 emails per turn, however the model splits the calls | `guard.py` |
| Confirmation | Moving mail to the Trash, an undo, more than 5 emails changed in one turn, forgetting a memory and cancelling a reminder run in two steps. The request is written by code, not by the model. For an undo and a reminder the later message has to be only a yes ("ok wait, no" or "Si può fare domani?" does not confirm). For the Trash, a forgotten memory and a bulk change it has to carry the four-digit code in the request | `guard.py` |
| No repeated updates | The webhook claims each Telegram update id with a conditional write, so a redelivery does not start a second turn | `lambdas/common.py`, `webhook.py` |
| Capped concurrency | Reserved concurrency of 5 on the webhook and 1 on the worker, so a flood is throttled before it reaches the model | `infra/lib/gmail-assistant-stack.ts` |
| No links from strangers | In the digest, the reminders and the reader's output, every link except one to Gmail is removed, because the text was written from raw email |
| No model output in logs | The agents are built with `callback_handler=None`, so Strands does not print what the model writes, and the runtime log group created by AgentCore gets a one-month retention | `agent.py`, `infra/lib/gmail-assistant-stack.ts` | `agent/gmail_assistant/links.py` |
| Visibility | After each change the code adds a fixed line to the reply, and the action log keeps a hash of the message that asked for it | `main.py`, `actions.py` |

Each principal has least privilege: the runtime, the worker and the webhook can read only the parameters they need, and `kms:Decrypt` is allowed only through SSM for those parameters. The runtime role can use four DynamoDB actions, one model, one guardrail, create, get and delete only schedules with the reminder prefix, and on the memory resource it has seven actions (events, record retrieval and listing, one record deletion), with no way to change the memory itself. `scheduler:ListSchedules` is still on `*`, because the action supports no resource or condition key. Keeping the reminders in DynamoDB would remove it; for this test project the risk is accepted.

Telegram is protected separately: the webhook checks the secret token, accepts only a private chat from your own account, and logs what it rejects without the text.

Not added, on purpose: API Gateway with an IP allowlist, and WAF. WAF attaches only to a REST API and costs about $6 a month fixed, more than the idle system, so the cap is reserved concurrency, which is free. The blog post explains the options.

Known limits: the reader is a model reading hostile text, so its output is bounded but not guaranteed clean. The request check is made of keywords in Italian and English. The guard's state lives in the memory of the session, so a restart empties it and the agent has to list the emails again.

## Prerequisites

- AWS account with credentials for `eu-west-1` (or change the region in `infra/bin/app.ts`), CDK bootstrapped
- Node.js 20+ and Python 3.13
- Docker running locally (the agent image is built for ARM64 during `cdk deploy`)
- A Telegram account and the Gmail account you want to manage. A personal Gmail account is enough: no Google Workspace and no billing are needed.

## Setup

### 1. Create the Telegram bot and find your chat ID

1. In Telegram, open `@BotFather`, send `/newbot`, and keep the token it gives you.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `result[0].message.chat.id`. That is your `ALLOWED_CHAT_ID`. If `result` is empty, send the bot another message and reload the page.
   The URL contains the bot token, so do not share it.

### 2. Create Google OAuth credentials

This is the only step that uses Google Cloud Console. It is free and does not require billing.

1. In Google Cloud Console, create a project and enable the **Gmail API**.
2. Configure the **OAuth consent screen** (External). Add your Gmail address under *Test users*.
   **Important:** while the app is in *Testing*, Google expires refresh tokens after 7 days, and only the listed test users can sign in (otherwise you get `403 access_denied`). Set the publishing status to *In production* to avoid the expiry. For personal use you will see an "unverified app" warning, which is expected.
3. Create credentials → **OAuth client ID** → application type **Desktop app**, and download the JSON file (`client_secret_*.json`).
   Keep the file outside the repository (for example in `~/Downloads`) and do not commit it. After `gmail_oauth_setup.py` has run, you can delete it.

### 3. Deploy

```bash
cd infra
npm ci
npx cdk bootstrap aws://<ACCOUNT_ID>/eu-west-1   # first time only
npx cdk deploy -c allowedChatId=<YOUR_CHAT_ID>
```

Optional context: `-c modelId=<enabled model id>`.

Optional cost budget, off by default: set `BUDGET_EMAIL` (and `BUDGET_LIMIT_USD`, default 10) when you deploy, for example `BUDGET_EMAIL=you@example.com BUDGET_LIMIT_USD=5 npx cdk deploy -c allowedChatId=<YOUR_CHAT_ID>`. It creates a monthly cost budget that emails you at 80% of the actual spend and 100% of the forecast. Every resource carries the tag `project=gmail-assistant` and the budget counts only that tag, so activate it as a cost allocation tag in the Billing console. The Nova Pro calls go through a system inference profile, which cannot be tagged, so the model cost is not in the budget.

Optional alarms, off by default: set `ALARM_EMAIL` (and `ALARM_TURNS_PER_HOUR`, default 30) to create five CloudWatch alarms that email you through SNS: too many agent turns in an hour, the webhook throttled at its concurrency cap, a message from a chat other than yours, the guard refusing changes nobody asked for (`ALARM_REFUSALS_PER_HOUR`, default 3), and too many changes in a day (`ALARM_CHANGES_PER_DAY`, default 30). Confirm the SNS subscription email before the alarms can reach you.

The digest is written in the language of your latest chat message, and Italian until you write something.

### 4. Store the secrets

Secrets live in SSM Parameter Store as SecureString parameters under `/gmail-assistant/`. Each parameter is encrypted with the AWS managed key, and it is deleted immediately when you tear the stack down.

```bash
pip install -r scripts/requirements.txt

# Asks for the bot token on the terminal (hidden input) and generates the webhook secret.
python scripts/put_secrets.py

# Opens a browser for Google consent and stores the Gmail refresh token.
python scripts/gmail_oauth_setup.py --client-secrets ~/Downloads/client_secret_XXXX.json
```

CloudFormation cannot create SecureString parameters, which is why they are created by these scripts and not by the stack.

### 5. Register the Telegram webhook

Use the `WebhookUrl` output from the deploy:

```bash
python scripts/set_telegram_webhook.py --url <WebhookUrl>
```

Now write to your bot in plain language, for example "list my unread emails", or "give me the digest". To test the digest without waiting for 10:00, invoke the worker:

```bash
aws lambda invoke --region eu-west-1 --function-name <WorkerFunctionName> \
  --cli-binary-format raw-in-base64-out --payload '{"type":"daily_digest"}' /dev/stdout
```

## Removing everything

```bash
python scripts/teardown.py
```

This runs `cdk destroy`, deletes the `/gmail-assistant/*` parameters, deletes any legacy Secrets Manager secrets, and checks that nothing is left. The CloudWatch log group that AgentCore creates for the runtime (`/aws/bedrock-agentcore/runtimes/gmail_assistant-*`) is not created by the stack (the stack only sets its retention), so delete it by hand if you want it gone. The DynamoDB table and the AgentCore Memory resource are part of the stack, and `cdk destroy` removes them too.

## Chat examples

- `Show me my unread emails from the last 2 days`
- `Read number 3`
- `Mark 1 and 4 as read`
- `Move the newsletters from this list to the folder Newsletters`
- `What folders do I have?`
- `Send me the PDF attached to the last email from the clinic`
- `Give me the digest`
- `Remind me about number 2 on Monday at nine`
- `Show me the reminders I have set`
- `What do you remember about me?`
- `Forget the Amazon preference`
- `Undo the last thing you did`

## Development

```bash
# Agent unit tests
cd agent && uv venv .venv && uv pip install -r requirements.in pytest && PYTHONPATH=. .venv/bin/python -m pytest tests

# Lambda helper tests
cd lambdas && uv run --with pytest --with boto3 python -m pytest test_common.py

# Type-check and synthesize the stack
cd infra && npm run build && npx cdk synth -c allowedChatId=123
```

## Cost and limits

- Idle cost is low: Parameter Store SecureString (standard tier) is free, plus CloudWatch logs and a daily digest run.
- Model prices, EU on-demand, per million tokens, from the AWS Pricing API: Nova Pro costs 0.92 USD for input and 3.68 USD for output. Prompt caching, batch and flex prices are not included.
- AgentCore Runtime, AgentCore Memory and Bedrock are billed per use. A digest run reads up to 50 emails and makes a few model calls.
- Telegram messages are split into chunks of about 3000 characters of text, and the chunks are sent in order.
- Telegram bots can send files up to 50 MB. Larger attachments are rejected.

## Troubleshooting

- **`invalid_grant` from Gmail:** the refresh token expired or was revoked. Re-run `scripts/gmail_oauth_setup.py` (see step 2 about Testing mode).
- **No reply in chat:** check the worker's CloudWatch logs. Also check that the webhook was registered (`getWebhookInfo` should show your URL and no `last_error_message`).
- **Messages from another chat are ignored by design.** The chat ID is checked on every update.
- **Parameter not found:** run `scripts/put_secrets.py` and `scripts/gmail_oauth_setup.py` before using the bot.
- **`403 access_denied` during Google sign-in:** the app is in *Testing* and your address is not a test user. Add it under *Test users*, or publish the app (see step 2).
- **`AccessDeniedException ... is not available for this account` from Bedrock:** the model is not enabled for your account. Pick an enabled model with `-c modelId=...` and redeploy.
- **Old model or old behaviour after a redeploy:** AgentCore keeps a session's microVM alive, and that microVM keeps the configuration it started with. Stop the session (`aws bedrock-agentcore stop-runtime-session`), or wait until it times out. The session id for a Telegram chat is the SHA-256 of `chat:<chat id>`.
- **The assistant answers from an old conversation:** the chat keeps its short-term history in AgentCore Memory. To start clean, delete the events of the session and the long-term records (see `agent/gmail_assistant/memory.py` for the namespaces).
- **`CERTIFICATE_VERIFY_FAILED` in `set_telegram_webhook.py`:** Python cannot verify the certificate chain. Run it with the system bundle: `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/set_telegram_webhook.py --url <WebhookUrl>`.

## Dependencies and the hostile-email set

`agent/requirements.in` lists the direct dependencies, and `agent/requirements.txt` is compiled from it with every package pinned and hashed (the command is in its header). The image installs it with `--require-hashes`. To check it for known vulnerabilities: `pip-audit -r agent/requirements.txt --no-deps --disable-pip`.

`agent/evals/hostile_emails.py` sends a small set of hostile and ordinary emails through the guardrail and the reader and prints what each one stopped. It calls Bedrock and never touches a mailbox: `cd agent && GUARDRAIL_ID=... GUARDRAIL_VERSION=... AWS_REGION=eu-west-1 .venv/bin/python -m evals.hostile_emails`.

`agent/evals/facts.py` does the same for ordinary emails: it checks that the reader keeps the amounts, dates and names: `cd agent && AWS_REGION=eu-west-1 .venv/bin/python -m evals.facts`.

## Emergency stop, SBOM and base image

`python scripts/pause.py pause` sets the concurrency of the webhook and the worker to 0 and disables the daily digest, so nothing reaches the agent. `resume` puts them back and `status` shows the state. Reminders that fire during the pause are dropped. A `cdk deploy` also sets the concurrency back.

`agent/sbom.cdx.json` lists the components of the image, generated from the lock: `pip-audit -r agent/requirements.txt --no-deps --disable-pip -f cyclonedx-json -o agent/sbom.cdx.json`. The Dockerfile pins the base image by digest and the container runs as a user without root rights. To update the base image, take the new digest with `docker buildx imagetools inspect public.ecr.aws/docker/library/python:3.13-slim`.

## Audit trail

Every change that runs, and every change the guard refuses, is written to the log group `/gmail-assistant/audit` (kept for a year): the event, the action, a count and a hash of your message, never the text of an email. The runtime's role can create streams and add lines, and cannot change or delete them. It is on whenever the stack is deployed. A refusal for an email that was not listed is logged but not counted by the alarm, because a model can slip that way.

`agent/evals/digest.py` runs the real digest agent on a small labelled mailbox and scores the sections, the reminders it proposes and what it invents: `cd agent && AWS_REGION=eu-west-1 .venv/bin/python -m evals.digest 5`.
