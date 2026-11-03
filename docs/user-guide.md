# User guide

## Search for a technician

1. Open http://localhost:8000.
2. In **What's on your to-do list?**, describe the job in plain words, for example "my shower drain is clogged" or "patch a hole in drywall". Use at least 3 characters.
3. Leave **City or ZIP** as Santa Clara or change it.
4. Click **Search**.

You can also click a quick-search button under the search bar, or a tile under **Popular home repair projects**. Both fill in the search and run it.

Search works by meaning, not exact words, so "leaky faucet" finds plumbers even if a profile says "plumbing fixtures".

## Read the results

The top 3 matching technicians appear as cards. Each card shows:

| Part | Meaning |
| --- | --- |
| Name, stars and review count | From the profile's source platform |
| **Free estimate** badge | The profile says estimates or consultations are free. This does not mean the repair is free |
| **✓ License listed** badge | The profile includes license evidence |
| **⚠ Verify license** badge | The license is in conflict, unverified, unknown or listed as unlicensed. Check before hiring |
| **License not published** badge | No license information was found |
| **Checked** badge | When the profile was last verified |
| Details list | Services, service area, fee, license number, availability, phone and email. Missing values show "Not published" |
| Yellow note | Verification gaps or caveats recorded for this profile |
| **Call**, **Email**, **Website** | Appear only when the profile has that contact detail |

## AI recommendation

The **AI recommendation** panel fills in after the cards, usually within a minute. It is written by a Qwen model running on the server, using only the 3 profiles shown. It explains why each provider matches and cites them as [1], [2], [3].

The recommendation can be wrong or incomplete, and the profile data may be out of date. Always confirm licensing, prices and availability with the provider before hiring. You can check California contractor licenses with the [CSLB license lookup](https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx).

## Request a repair (tenants)

Use this when you rent and want the property owner to arrange a repair.

1. Open http://localhost:8000/request, or click **Request a repair** in the site header.
2. Describe the problem in your own words, check the property location, and optionally add your name.
3. Click **Submit request**.

The page now tracks your request. **Bookmark it**: its address (`/request?job=...`) is the only way back to your request. A progress bar shows the seven steps:

| Step | What happens | You need to |
| --- | --- | --- |
| Translate | Your words become a service request, shown under **Service request** | Check it reads right |
| Search & Match, Owner Approval, Pricing, Pricing Approval | Technicians are found and the owner approves one and its price | Wait. The page updates by itself |
| Scheduling | **Pick times for the visit** appears | Enter 2 or 3 time slots, e.g. "Mon Nov 9, 9–11am", and click **Send time slots** |
| Job Confirmation | The owner confirms the visit with the technician | Wait for the owner or technician to contact you |

Urgent hazards such as sparks, a gas smell or flooding are always marked as emergencies. If you're in danger, call emergency services first.

## Tips

- Name the job, not just the trade: "replace a broken outlet" works better than "electrician".
- If results look off, rephrase or add detail, such as the room or material.
- Starting a new search replaces the previous results.
- On the request page, include where the problem is and what you've noticed ("water under the kitchen sink cabinet"). It helps match the right trade.
