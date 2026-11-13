# container_scripts

`weekly_todo_extract.py` runs **inside** the OpenClaw container (alongside
`nocodb_briefing.py` / `email_briefing.py`) and sends the week's WhatsApp
transcript to the DeepSeek API to extract ad-hoc to-do items not already
tracked in NocoDB. Because that's a real outbound call carrying internal
chat content, deploying it is a deliberate one-time step, not something the
host-side automation does for you.

## Deploy (one-time)

Copy this file to the container's bind-mounted config directory:

```
copy weekly_todo_extract.py "D:\AI_Bot\openclaw\data\config\weekly_todo_extract.py"
```

That directory is mounted into the container at `/home/node/.openclaw/`, so
it becomes `/home/node/.openclaw/weekly_todo_extract.py` immediately — no
container restart needed.

## Without this step

`WeeklyTodoReport/runners/weekly_report.py` calls it via `docker exec` each
run. If the file isn't deployed yet, that call fails harmlessly and the
report still goes out with the NocoDB sheet populated and the chat-derived
sheet empty (or pass `--skip-chat-extract` to skip the attempt entirely).

## Updating

Edit the copy in this repo, then re-copy it over the deployed one — same
one-line copy command above.
