# Task: switch the owner's live ROSERY to the self-updating runner (2026-10-23), from Claude (cloud)

The owner asked for this to be handed to you; you are the agent on his computer now. Talk to him in Hebrew.
Claude in the cloud cannot reach the computer, which is why this comes to you.

## The owner's authorization for this task (and only this task)
`.agents/README.md` forbids agents to run the server against the live data folder. For this task only the
owner authorized, in the cloud chat, stopping the two running processes below and starting `run_owner.py`
with that same live data folder. Confirm it with him in your chat before step 3. Nothing else changes:
no Meta or Shopify writes, no execution workers, do not read, print or copy credentials (`*.dpapi`,
tokens, `auth.json`, `phone-gateway.json`) and do not edit rules.

## Where things are now (read on the computer just now)
- Main checkout: `C:\Users\ibaka\Documents\rosery-chatbot`, branch `claude/project-understanding-hg3b34`.
  The owner already ran `git pull` once (now at `0eb5c23`). `.agents/events.jsonl` has local edits (yours).
- Running now (old code):
  - `python.exe -B server.py --workspace manager --port 8767 --no-startup-execution-workers --data-dir "C:\Users\ibaka\Documents\Codex\2026-10-07\meta-ads-plugin-dev-6ab288ee4844819188ab85d774f94f22-created-2\campaigner\.local"`
  - `python.exe -B phone_gateway.py --bind-ip 100.108.27.66 --phone-ip 100.112.97.75 --data-dir "<same folder>"`
- The phone shows the old screen because those processes still run the old code.

## Steps
1. In the main checkout: `git pull origin claude/project-understanding-hg3b34` (must fast-forward to `6162128`
   or later; it includes the fix so `.agents/events.jsonl` edits do not block updates).
2. Ask the owner whether anything is mid-way on the screen (an approval, an upload to Meta). If yes, wait.
3. Stop only the two processes above (match `server.py` / `phone_gateway.py` in the command line).
4. From `campaigner\`, start once, and leave the window open:
   `python -B run_owner.py --data-dir "C:\Users\ibaka\Documents\Codex\2026-10-07\meta-ads-plugin-dev-6ab288ee4844819188ab85d774f94f22-created-2\campaigner\.local" --phone-bind-ip 100.108.27.66 --phone-ip 100.112.97.75`
   It saves these settings to `campaigner\.local\run-owner.json`; later the owner only double-clicks `START-ROSERY.bat`.
5. Check: `http://127.0.0.1:8767/` loads; the page foot shows `גרסה 6162128` (or later); on the phone the foot
   line shows the same and the home screen starts with the period picker (היום / אתמול / 3 ימים / 7 ימים).
   The phone should stay signed in (same data folder, same gateway state); if it asks for a code, tell the owner
   instead of making a new one.

## What run_owner.py does (so you can explain it)
Runs the server and the phone gateway; every 5 minutes (or on "בדוק עדכון עכשיו" on the screen) fetches the
branch. Takes a new version only if it fast-forwards, no locally edited file is also changed by the update,
a smoke check passes (server on a spare port with an empty temp folder, screen loads; otherwise `reset --keep`
back), and the server reports nothing half done (approved Meta change, upload to Meta, content analysis, chat
answer, file upload). Status shows at the foot of the screen; a banner offers "רענן עכשיו" after an update.
Tests: `test_run_owner.py`, `test_update_routes.py`.

## If something fails
Do not improvise around it. Restart the two old commands above exactly as they were (the owner keeps a
working system), and write what happened to `.agents/messages/claude/` so the cloud session can fix it.
