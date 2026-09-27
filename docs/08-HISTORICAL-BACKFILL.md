# Historical Backfill

Personal State's live connectors preserve data from the moment they are installed. They do not guarantee access to every record a vendor has retained. A complete historical backfill uses each vendor's official account export and then resumes incremental collection.

## Safety and Integrity Rules

- Keep the original archive unchanged as the source artifact.
- Preview and validate coverage before writing to the production database.
- Preserve source, device, timestamp, unit, and import provenance.
- Deduplicate overlap with records already collected live.
- Never turn an old imported value into a live reading.
- Report rejected rows and missing file categories.
- Back up the SQLite database immediately before a production import.

## LibreView

Abbott does not expose years of history through the LibreLinkUp follower interface. Sign in to LibreView with the account used by the official Libre app, open **Glucose History**, and select **Download Glucose Data**. This produces a CSV containing the historical glucose records retained by LibreView.

Preview:

```powershell
personal-state import-libreview C:\path\to\glucose.csv --dry-run
```

Import after the preview dates and counts are correct:

```powershell
personal-state import-libreview C:\path\to\glucose.csv
```

Device timestamps are interpreted with `PERSONAL_STATE_MCP_SOURCE_TIMEZONE` unless `--timezone` is supplied. The importer handles mg/dL and mmol/L exports, validates bounds, hashes device identifiers, and deduplicates matching timestamp/value pairs across adapters.

## Samsung Health and Galaxy Watch

On the phone, open **Samsung Health > More options > Settings > Download personal data > Download**. Samsung places the archive under **My Files > Downloads > Samsung Health**. Copy the complete folder or ZIP to the computer without renaming or editing individual files.

The Samsung Health Data SDK and Health Connect remain useful for ongoing sync, but their returned history can be shorter than the account archive. The official archive is therefore the authority for the one-time backfill.

## Google Fit

Use Google Takeout while signed into the Google account that actually owns the Fit history. Deselect all products, select **Fit**, create a one-time ZIP export, and download every archive part. Workspace administrators may disable Fit exports; in that case use the owning personal Google account or have the administrator enable the service.

Preview the ZIP or extracted Takeout directory:

```powershell
personal-state import-google-fit C:\path\to\takeout.zip --dry-run
```

Import after checking the coverage and per-metric counts:

```powershell
personal-state import-google-fit C:\path\to\takeout.zip
```

The importer reads raw heart rate, oxygen saturation, sleep stages, blood pressure, weight, body fat, height, and speed records. It also imports Google Fit daily activity summaries, derived resting heart rate and basal metabolic rate, and TCX exercise sessions. High-volume heart and speed samples are stored in daily series records so the original measurements remain available without creating hundreds of thousands of database rows.

Google's location traces and opaque device sensor-event logs are intentionally excluded because they are not physiological dashboard measurements. They remain unchanged in the original Takeout archive. Imported records use the `google_fit_takeout` adapter and are always presented as historical or latest-recorded data, never as live device data.

After import, use the dashboard's **All** range to view coverage older than one year. Re-importing the same or an overlapping Takeout archive is supported; exact records are skipped.

## Archive Inspection

Before importing a Google or Samsung archive, identify it and record its file inventory:

```powershell
personal-state inspect-history-export C:\path\to\archive.zip
```

Google and Samsung formats can vary by account, app version, region, and metric. Their production import mapping must be validated against the actual archive rather than inferred from filenames. Unsupported fields remain in the original archive and are reported instead of silently discarded.

## Completion Criteria

A backfill is complete only when:

1. The earliest and latest dates for every metric are reported.
2. Imported, duplicate, and rejected counts are recorded.
3. Original archive files are retained.
4. Dashboard charts show historical coverage without labeling imported records as current.
5. A second import of the same archive inserts zero duplicate records.
