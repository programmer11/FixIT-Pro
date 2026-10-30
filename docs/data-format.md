# Profile data format

A profiles file is a JSON array. Each element is one technician profile: an object whose keys are field names and whose values are strings.

## Rules

- The file must be valid JSON and at most 5 MB.
- The array must contain at least one profile.
- Every profile needs a non-empty **`ID`** and **`Name / company`**.
- `ID` values must be unique within the file. The ID decides which Pinecone document is updated (`technician-<ID>`), so keep IDs stable between uploads.
- Any other fields are optional. Empty strings and `null` values are left out of the index.
- Every field, including ones not listed below, is indexed and can influence search.

## Fields shown in the app

| Field | Shown as | Notes |
| --- | --- | --- |
| `ID` | (not shown) | Required. Stable identifier |
| `Name / company` | Card title | Required |
| `Operator type` | Under the name | e.g. Company, Individual |
| `Source platform` | Under the name | e.g. Angi, Nextdoor, Direct website |
| `Rating / 5` | Stars | A number such as `4.5` |
| `Review count` | "N reviews" | |
| `Services provided` | Services | Main text for matching jobs; be specific |
| `Service areas / location` | Service area | Include city names for location matching |
| `Free estimate / consultation` | **Free estimate** badge | Badge shows when the value starts with "Yes" |
| `Service fee / minimum` | Service fee / minimum | |
| `License evidence` | License badge and row | Words like "conflict", "unlicensed", "not verified" or "unknown" show **⚠ Verify license** |
| `License number` | License number | |
| `Availability / emergency` | Availability | |
| `Phone` | Phone, **Call** button | The first phone number found is used for the button |
| `Email` | Email, **Email** button | The first email address found is used for the button |
| `Additional source`, `Website` | **Website** button | Only full `http://` or `https://` URLs become a button |
| `Notes / verification gaps` | Yellow note | |
| `Checked on` | **Checked** badge | Date the profile was last verified |

Use the text `Not published` for values you looked for but couldn't find. The app displays it greyed out, and the AI recommendation repeats it rather than guessing.

Other fields in the sample data, such as `Reviews / evidence`, `Response time` and `Source-to-field mapping`, are not shown on the cards but are searchable and available to the AI recommendation.

## Example

```json
[
  {
    "ID": "001",
    "Name / company": "Example Handyman LLC",
    "Operator type": "Company",
    "Source platform": "Direct website",
    "Rating / 5": "4.5",
    "Review count": "12",
    "Service areas / location": "Santa Clara, San Jose",
    "Services provided": "Plumbing fixtures; drywall; TV mounting",
    "Free estimate / consultation": "Yes",
    "Service fee / minimum": "Not published",
    "License evidence": "Not verified",
    "License number": "Not published",
    "Availability / emergency": "Contact to confirm",
    "Phone": "555-555-0100",
    "Email": "hello@example.com",
    "Additional source": "https://example.com/",
    "Notes / verification gaps": "Confirm license before contracting.",
    "Checked on": "2026-10-01"
  }
]
```
