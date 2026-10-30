# Troubleshooting

### The browser says "connection refused" or "Failed to load page"

The server isn't running. Start it from the project folder and keep the terminal open:

```bash
python3 -m uvicorn app:app --port 8000
```

Wait for `Uvicorn running on http://127.0.0.1:8000`, then reload the page.

### The server won't start: "Set PINECONE_API_KEY…" or "Set PINECONE_INDEX_NAME…"

`.env` is missing, isn't in the project folder, or lacks that variable. See [Setup and configuration](setup.md#configure).

### The server won't start: "address already in use"

Another process is using port 8000, often an earlier FixIT-Pro server. Stop it, or run on another port with `--port 8001`. To find what's using the port on macOS or Linux: `lsof -iTCP:8000 -sTCP:LISTEN`.

### Searches return no matches

- The Pinecone index has no profiles yet. Upload some on the [admin page](admin-guide.md).
- You uploaded moments ago. New profiles can take a few seconds to become searchable.
- `PINECONE_INDEX_NAME` points at a different index than the one you uploaded to.

### The AI recommendation says the model file can't be found

Set `QWEN_MODEL_PATH` in `.env` to the full path of your `.gguf` file, then restart the server.

### The AI recommendation takes a long time

This is expected on CPU. The first request after startup loads the model; later ones are faster. Requests are processed one at a time, so several searches in a row queue up. Provider cards don't wait for the recommendation.

### The admin page says "Admin is disabled"

Add `ADMIN_PASSWORD` to `.env` and restart the server. The server reads `.env` only at startup.

### The admin page says "Incorrect admin password"

The password must match `ADMIN_PASSWORD` exactly, including case. If you changed it in `.env`, restart the server.

### An upload fails with a validation error

The message names the problem and the item number (counting from 1). Common causes: a missing `ID` or `Name / company`, a duplicate `ID`, or a trailing comma that makes the JSON invalid. See [Profile data format](data-format.md).

### A technician I removed from the file still shows up

Uploads add and update profiles but never delete them. Delete the document `technician-<ID>` in the Pinecone console. See [Removing a technician](admin-guide.md#removing-a-technician).
