# Path Safety Note

There are two receipts packages. Do not confuse them.

## Production Package

Production path:

```text
\\file-server\Shared Folder\Scripts\receipts_pilot_v1.0
```

Production user entry:

```text
\\file-server\Shared Folder\Scripts\receipts_pilot_v1.0\run_receipts.bat
```

Rules:
- Do not modify production directly during dev work.
- Do not run tests against active client folders.
- Do not deploy while staff may be running `run_receipts.bat`.
- Do not copy dev files into production unless deployment is approved.

## Dev Package

Dev path:

```text
\\file-server\Shared Folder\Scripts\receipts
```

Use this package for:
- code changes
- documentation
- artificial smoke tests
- checkpoint/resume development
- IIF validate-only development

## Command Directory Rules

Run dev module commands from:

```powershell
Set-Location "\\file-server\Shared Folder\Scripts"
```

Then use:

```powershell
python -m receipts...
```

Reason:
- From `Scripts`, Python imports the dev package at `Scripts\receipts`.
- From `receipts_pilot_v1.0`, Python imports production package code.

## Common Mistakes To Avoid

Do not run dev tests from:

```powershell
Set-Location "\\file-server\Shared Folder\Scripts\receipts_pilot_v1.0"
```

unless the explicit goal is a production/staging verification after approval.

Do not use:

```text
receipts_pilot_v1.0\receipts
```

as the active dev folder.

Do not create checkpoint smoke-test files inside active client folders.

Do not assume `receipts` and `receipts_pilot_v1.0\receipts` are the same package.

## Current Role Of Each Package

Production:
- actively used by staff
- stable user entry
- deployment target only after approval

Dev:
- current development source
- checkpoint/resume hotfix work
- IIF validate-only work
- documentation source

