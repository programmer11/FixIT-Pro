# Admin guide

The admin page lets you check how many profiles are loaded and, optionally, upload a new or updated profiles file. Uploading is only needed when technician data changes. The **owner console** is where the property owner reviews tenant repair requests. It uses the same password.

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

## Owner console: approve repair requests

Open http://localhost:8000/admin/jobs, or click **Review service requests** on the admin dashboard. Requests are listed on the left, newest first. An orange dot means the request is waiting for you. The list refreshes every 15 seconds.

Click a request to see its progress bar, the current step, the service request and the **Agent activity** log. When it's your turn, the step's form appears at the top:

### 1. Approve the top technicians

Each card shows the **business or technician name**, its **website** and its source. Database technicians show their platform. Internet finds are marked **Found on the internet · not verified**. The card also shows an overall score with bars for job fit, licensing, reviews and cost, the reasons behind them, and the services, area, license, fee and contact details.

- A website marked *found by internet search* was matched by name. Open it and check it's the same business.
- **More from the internet search** lists other businesses and directory pages found online.
- Click **Approve these technicians**, or tick **Reject this technician** on any cards and click **Reject checked (or all) and search again**. With nothing ticked, all 3 are rejected. Rejected technicians never come back for this request, so you can keep rejecting until the list is right.

### 2. Get pricing quotes

The Pricing agent has drafted a quote request for each technician. Use **Email** (opens your mail app with the message filled in), **Call** or **Website** to contact them. When they reply, enter each price and any notes, then click **Save quotes**. Leave a price blank if a technician didn't reply; at least one is needed.

### 3. Choose the final technician

Quotes are listed lowest first. Pick one and click **Select technician**, or **Reject all and search again**.

### 4. Wait for the tenant

The tenant is asked for 2–3 time slots on their request page. If they don't have the link, click **Tenant's page** under the service request and send them that address.

### 5. Approve the scheduling message

The Job Confirmation agent drafts a message to the technician with the job, price and the tenant's time slots. Edit the text directly and click **Approve message**, or describe what to change and click **Request changes** to have it redrafted.

Once approved, the request is **Confirmed** and the final message is shown with email, call and website links. The app doesn't send it for you.

### If a step fails

A request shows an error, or says it was interrupted (for example by a server restart during a step). Click **Retry from last step**. Nothing that was already approved is lost.

## Security notes

- The admin password is a single shared secret, sent with each admin request and kept in the browser tab's session storage.
- This is suitable for running on your own machine. Before putting FixIT-Pro on a network or the internet, add HTTPS and proper user authentication, and consider rate limiting the login.
