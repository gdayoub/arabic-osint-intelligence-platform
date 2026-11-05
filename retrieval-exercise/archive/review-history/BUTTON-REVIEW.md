# Button review page

Open http://127.0.0.1:8874 while the local server is running.

1. Read the question, English gloss, and Arabic passage.
2. Select Relevant, Partly relevant, Irrelevant, or Unsure.
3. Add a note if useful, then click Approve & next.

Selections and notes are saved as drafts on this computer. Approval confirms the judgment. Editing an approved review makes it a draft again. Arabic verification is a separate, optional checkbox.

The page writes to button-review-history.jsonl beside this file. Each save appends an event; earlier decisions are retained. Export reviews downloads a structured JSON file with confirmed labels and draft reviews distinguished. Original review-40.json, original article text, and older Markdown reviews are unchanged. Use the button interface as the review source of truth for this batch; do not edit both interfaces independently.

After restarting the computer, open a terminal in this folder and run the already-installed Node runtime:

```powershell
& 'C:\Users\george\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\review-server.cjs
```

Then open the local address above. Leave that process running while reviewing. No installation is needed.

The server binds only to 127.0.0.1. No data is sent to Neon, R2, or an external model. Reviews remain in evaluation-candidate quarantine and cannot become training examples merely by pressing Approve.

UI verification used --test on port 8875 with a separate ui-test-history.jsonl. Test approvals are synthetic and are never imported into the real review journal. Approval/advance, reload persistence, editing back to draft, and JSON export were checked. The live page was opened with 0/40 approved.
