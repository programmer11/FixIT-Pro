# Admin guide

The admin page lets you check how many profiles are loaded and, optionally, upload a new or updated profiles file. Uploading is only needed when technician data changes.

## Enable admin

Admin is off until a password is set:

1. Add `ADMIN_PASSWORD=<your password>` to `.env`.
2. Restart the server.

## Sign in

1. Open http://localhost:8000/admin, or click **Admin** in the site header.
2. Enter the admin password and click **Sign in**.

The **Admin dashboard** shows how many profiles are in the local profiles file. You stay signed in until you close the browser tab or click **Sign out**.

## Upload profiles (optional)

1. On the dashboard, expand **Upload technician profiles (optional)**.
2. Click the dashed box to choose a `.json` file, or drag the file onto it.
3. Click **Upload to index**.

A green message confirms how many profiles were uploaded. New results can take a few seconds to appear in search.

### What an upload does

1. Checks the file: at most 5 MB, valid JSON, a non-empty array, and every profile has a unique `ID` and a `Name / company`. If anything fails, nothing is uploaded and the error says which item is wrong.
2. Sends the profiles to Pinecone in batches of 50. A profile whose `ID` already exists is **replaced**; a new `ID` is **added**.
3. Backs up the current local file as `santa_clara_home_repair_profiles.<YYYYMMDD-HHMMSS>.bak.json`.
4. Saves the uploaded file as `santa_clara_home_repair_profiles.json`.

Both the local file and its backups are git-ignored, so profile data stays out of the repository.

### Removing a technician

Uploading a file without a profile does **not** remove that profile from Pinecone; it stays searchable. To remove one, delete the document `technician-<ID>` from the `santa-clara-technician-profiles` namespace in the Pinecone console.

### Restoring a previous version

Upload one of the `.bak.json` backup files from the admin page. Profiles added since that backup stay in Pinecone (see above).

## Security notes

- The admin password is a single shared secret, sent with each admin request and kept in the browser tab's session storage.
- This is suitable for running on your own machine. Before putting FixIT-Pro on a network or the internet, add HTTPS and proper user authentication, and consider rate limiting the login.
