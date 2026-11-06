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

## Repair request workflow

### A request stays on "working" for a long time

Each agent that writes text calls the local model, which takes 20–60 seconds on CPU, and other searches or requests queue for the same model. If it's still working after several minutes, check the server terminal for errors.

### A request shows "This job was interrupted" or an error

The server stopped during a step, or a step raised an error (for example Pinecone was unreachable). Open it in the owner console and click **Retry from last step**. It continues from the last saved step.

### "This step is not waiting for input"

Someone already answered the step, or an agent is still working. Reload the page to see the current state.

### No internet businesses or websites appear

The free search backend returned nothing, which happens when it's rate-limited. The workflow carries on with database technicians. Results are cached for an hour, and later rejection rounds search again.

### A website link is for the wrong business

Links marked *found by internet search* are matched by name and can be wrong when names are similar. Ignore the link, or reject the technician. To fix it permanently, add the correct URL to the profile's `Additional source` field and upload the profiles again.

### The message drafts read oddly

Pricing and Job Confirmation messages come from a small local model. Edit the final message before approving it, or use **Request changes**. If the activity log says *template*, the model file wasn't found. Check `QWEN_MODEL_PATH`.

### The tenant lost their request link

Open the request in the owner console, click **Tenant's page** under the service request, and send the tenant that page's address.

### A photo or video won't upload

The page names the file and the reason:

- **"isn't a supported photo or video"**: the file isn't JPEG, PNG, GIF, WebP, HEIC, AVIF, MP4, MOV or WebM. This includes a file renamed to look like a photo. Convert it, or take a screenshot of it.
- **"larger than 15 MB" / "larger than 100 MB"**: photos are limited to 15 MB and videos to 100 MB. Trim the video or send a shorter clip.
- **"at most 8 photos and videos"**: the request already has 8 files.
- **"already confirmed"**: files can't be added after the visit is confirmed. Send them to the owner directly.

### A photo shows "no preview — download"

It's a HEIC photo (the iPhone default). Most browsers other than Safari can't display HEIC. Download it and open it on your computer, or set the iPhone camera to **Most Compatible** (Settings → Camera → Formats) to take JPEGs.
