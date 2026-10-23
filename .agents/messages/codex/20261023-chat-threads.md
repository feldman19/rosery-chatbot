Owner request (2026-10-23): the Ask tab is one endless thread; after a week he scrolls through everything. Split it into conversations by topic. Fix on your branch, tests, do not commit, Hebrew report. No Meta writes, no rules edits, UI Hebrew. Files: `campaigner/claude_chat.py` (turns table, `previous_turns`), `campaigner/server.py` (`/api/claude-chat`), `campaigner/simple-web.html` (Ask tab), `campaigner/phone_gateway.py` (add any new routes to its allowlist).

1. Threads: a `threads` table (id, title, kind, created_at, updated_at, archived). Existing turns migrate into one thread "שיחות קודמות" (nothing lost). Every turn belongs to a thread; `previous_turns` sent to the model come only from the same thread (still the last 4).
2. Kinds, chosen by the owner when starting or suggested from the first question: "מודעה מסוימת" (bound to an ad id; the chat context then includes that ad's rule assessment), "תכנון קמפיין", "מה עובד לי" (account history), "שאלה כללית". Show the kind as a small pill.
3. Title: from the first question, trimmed to ~40 chars at a word boundary (no extra model call); the owner can rename.
4. Ask tab: a list of conversations (newest first, with kind, title, last answer time, one-line preview), a search box over titles and questions, "שיחה חדשה" at the top, open a thread to see only its turns, "העבר לארכיון" with an "ארכיון" filter to bring it back. On the phone the list and the open thread are two screens with a back arrow.
5. Routes: list/search threads, create, rename, archive/unarchive, and the existing ask route takes `thread_id` (required for new turns; old clients without it go to the latest active thread). Same session/origin checks as today.
6. Tests: migration keeps old turns; context isolation between threads; search; archive hides and restores; ad-bound thread includes that ad's assessment.

Run the full suite (`cd campaigner && python -B -m unittest discover -p "test_*.py"`; baseline here 6 failures, 19 errors) and report.
