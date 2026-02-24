# Multi-Region Signoffs

This folder stores committed staging signoff results for multi-region validation.

## Expected Files
- `YYYY-MM-DD_run-<run_id>_multi_region_signoff.md` copied from the workflow artifact.
- `SIGNOFF_LOG.md` updated with one line per execution.

## Source Workflow
- `.github/workflows/multi_region_report.yml`

## Why this exists
This keeps staging execution/signoff evidence in-repo instead of only in ephemeral workflow logs.
