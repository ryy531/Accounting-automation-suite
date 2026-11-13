param(
    [switch]$Apply,
    [switch]$DiagnosticOne,
    [switch]$DiagnosticMinimal,
    [switch]$DiagnosticArray,
    [switch]$DiagnosticFieldIsolation,
    [string]$ReportDir
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvPath = Join-Path $Root "_lib\config\.env"
$ReportRoot = Join-Path $Root "payroll\_reports"
$DefaultMasterPath = "\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx"

$PhaseFields = @(
    "GST Filed Until",
    "GST Due Next Period",
    "PST Filed Until",
    "PST Due Next Period",
    "T2 Filed Until",
    "T2 Due Next Period",
    "Bookkeeping Filed Until",
    "Annual Return Month",
    "GST Due Next Period Calculated",
    "PST Due Next Period Calculated",
    "T2 Due Next Period Calculated",
    "Bookkeeping Due Date Calculated",
    "Workbench Next Action",
    "Workbench Next Action Date",
    "Workbench Needs Review",
    "Workbench Review Reason",
    "Workbench Last Updated At"
)

$ExcelToTarget = [ordered]@{
    "GST Filed Until" = "GST Filed Until 24"
    "GST Due Next Period" = "GST Due Next Period"
    "PST Filed Until" = "PST Filed Until"
    "PST Due Next Period" = "PST Next Due"
    "T2 Filed Until" = "T2 Filed Until 18"
    "T2 Due Next Period" = "T2 Due Next Period"
    "Bookkeeping Filed Until" = "Bookkeeping Done Until 15"
    "Annual Return Month" = "Annual Return"
}

$DateTargets = @(
    "GST Filed Until",
    "GST Due Next Period",
    "PST Filed Until",
    "PST Due Next Period",
    "T2 Filed Until",
    "T2 Due Next Period",
    "Bookkeeping Filed Until"
)

$ManagedWorkbenchFields = @(
    "Workbench Needs Review",
    "Workbench Review Reason",
    "Workbench Last Updated At"
)

$MonthValues = @{
    "jan" = "Jan"; "january" = "Jan"
    "feb" = "Feb"; "february" = "Feb"
    "mar" = "Mar"; "march" = "Mar"
    "apr" = "Apr"; "april" = "Apr"
    "may" = "May"
    "jun" = "Jun"; "june" = "Jun"
    "jul" = "Jul"; "july" = "Jul"
    "aug" = "Aug"; "august" = "Aug"
    "sep" = "Sep"; "sept" = "Sep"; "september" = "Sep"
    "oct" = "Oct"; "october" = "Oct"
    "nov" = "Nov"; "november" = "Nov"
    "dec" = "Dec"; "december" = "Dec"
}

function Read-EnvFile {
    param([string]$Path)
    $config = @{}
    Get-Content -LiteralPath $Path | ForEach-Object {
        if ($_ -match "^\s*#") { return }
        if ($_ -match "^\s*([^=]+?)\s*=\s*(.*)\s*$") {
            $key = $Matches[1].Trim()
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            $config[$key] = $value
        }
    }
    return $config
}

function Normalize-Text {
    param($Value)
    if ($null -eq $Value) { return "" }
    return (($Value.ToString() -replace "`r|`n", " ") -replace "\s+", " ").Trim()
}

function Normalize-Key {
    param($Value)
    return ((Normalize-Text $Value).ToLowerInvariant() -replace "\s+", " ").Trim()
}

function Is-BlankLike {
    param($Value)
    $text = (Normalize-Text $Value).ToLowerInvariant()
    return @("", "na", "n/a", "none", "-", "--", "null").Contains($text)
}

function Is-CurrentValueEmptyForField {
    param([string]$FieldName, $Value)
    $text = (Normalize-Text $Value).ToLowerInvariant()
    if ($FieldName -eq "Workbench Needs Review") {
        return @("", "0", "false", "no", "unchecked", "null").Contains($text)
    }
    return ($text -eq "")
}

function Convert-ToIsoDate {
    param($RawValue, [string]$FieldName)
    $text = Normalize-Text $RawValue
    if (Is-BlankLike $text) {
        return @{ Value = $null; Reason = "$FieldName is blank or marked NA" }
    }
    if ($RawValue -is [double] -or $RawValue -is [int]) {
        try {
            return @{ Value = ([DateTime]::FromOADate([double]$RawValue)).ToString("yyyy-MM-dd"); Reason = $null }
        } catch {
            return @{ Value = $null; Reason = "$FieldName has invalid Excel date serial: $text" }
        }
    }
    $formats = @("yyyy-MM-dd", "yyyy/MM/dd", "dd/MM/yyyy", "M/d/yyyy", "yyyy-MMM-dd", "yyyy-MMMM-dd", "yyyy-MM")
    foreach ($format in $formats) {
        try {
            $culture = [System.Globalization.CultureInfo]::InvariantCulture
            $parsed = [DateTime]::ParseExact($text, $format, $culture)
            if ($format -eq "yyyy-MM") {
                return @{ Value = (Get-Date -Year $parsed.Year -Month $parsed.Month -Day 1).ToString("yyyy-MM-dd"); Reason = $null }
            }
            return @{ Value = $parsed.ToString("yyyy-MM-dd"); Reason = $null }
        } catch {
        }
    }
    return @{ Value = $null; Reason = "$FieldName has invalid date value: $text" }
}

function Convert-ToMonth {
    param($RawValue)
    $text = Normalize-Text $RawValue
    if (Is-BlankLike $text) {
        return @{ Value = $null; Reason = "Annual Return Month is blank or marked NA" }
    }
    $key = $text.ToLowerInvariant()
    if ($MonthValues.ContainsKey($key)) {
        return @{ Value = $MonthValues[$key]; Reason = $null }
    }
    if ($key -match "([a-z]{3,9})" -and $MonthValues.ContainsKey($Matches[1])) {
        return @{ Value = $MonthValues[$Matches[1]]; Reason = $null }
    }
    return @{ Value = $null; Reason = "Annual Return Month has invalid value: $text" }
}

function Invoke-NocoGet {
    param([string]$Url, [hashtable]$Headers)
    return Invoke-RestMethod -Method Get -Uri $Url -Headers $Headers -TimeoutSec 30
}

function Invoke-NocoPatch {
    param(
        [string]$Url,
        [hashtable]$Headers,
        [object]$Body,
        [string]$ContentType = "application/json; charset=utf-8"
    )
    $json = $Body | ConvertTo-Json -Depth 8
    return Invoke-RestMethod -Method Patch -Uri $Url -Headers $Headers -Body $json -ContentType $ContentType -TimeoutSec 30
}

function Get-HttpErrorDetails {
    param($ErrorRecord)
    $status = $null
    $body = ""
    $response = $ErrorRecord.Exception.Response
    if ($response) {
        try {
            $status = [int]$response.StatusCode
        } catch {
            $status = $null
        }
        try {
            $stream = $response.GetResponseStream()
            if ($stream) {
                $reader = New-Object System.IO.StreamReader($stream)
                $body = $reader.ReadToEnd()
                $reader.Close()
            }
        } catch {
            $body = ""
        }
    }
    return [pscustomobject]@{
        status = $status
        response_body = $body
    }
}

function Get-AllNocoRows {
    param([string]$BaseUrl, [string]$TableId, [hashtable]$Headers)
    $rows = @()
    $offset = 0
    while ($true) {
        $url = "$BaseUrl/api/v2/tables/$TableId/records?limit=100&offset=$offset"
        $response = Invoke-NocoGet -Url $url -Headers $Headers
        $batch = @($response.list)
        $rows += $batch
        if ($batch.Count -lt 100) { break }
        $offset += 100
    }
    return $rows
}

function Read-MasterRows {
    param([string]$MasterPath, [string]$Password)
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.Calculation = -4135
    $workbook = $null
    try {
        $workbook = $excel.Workbooks.Open($MasterPath, 0, $true, 5, $Password)
        $sheet = $workbook.Worksheets.Item("Master")
        $used = $sheet.UsedRange
        $rowCount = [Math]::Min($used.Rows.Count, 1000)
        $scanCols = 200
        $values = $sheet.Range($sheet.Cells.Item(1, 1), $sheet.Cells.Item($rowCount, $scanCols)).Value2
        $headers = @()
        for ($c = 1; $c -le $scanCols; $c++) {
            $header = Normalize-Text ($values.GetValue(1, $c))
            if ($header) {
                $headers += [pscustomobject]@{ Name = $header; Col = $c }
            }
        }
        $rows = @()
        for ($r = 2; $r -le $rowCount; $r++) {
            $hasValue = $false
            $row = [ordered]@{ "__row" = $r }
            foreach ($header in $headers) {
                $value = $values.GetValue($r, $header.Col)
                $row[$header.Name] = $value
                if (Normalize-Text $value) { $hasValue = $true }
            }
            if ($hasValue) {
                $rows += [pscustomobject]$row
            }
        }
        return @{ Rows = $rows; Headers = @($headers.Name); UsedRange = $used.Address($false, $false) }
    } finally {
        if ($workbook) {
            $workbook.Close($false) | Out-Null
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) | Out-Null
        }
        $excel.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
}

function Add-UniqueReason {
    param([System.Collections.Generic.List[string]]$Reasons, [string]$Reason)
    if ($Reason -and -not $Reasons.Contains($Reason)) {
        [void]$Reasons.Add($Reason)
    }
}

if (-not $ReportDir) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $ReportDir = Join-Path $ReportRoot "phase1_client_workbench_$stamp"
}
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$config = Read-EnvFile -Path $EnvPath
$BaseUrl = $config["NOCODB_URL"].TrimEnd("/")
$Token = $config["NOCODB_TOKEN"]
$CompaniesTableId = $config["NOCODB_COMPANIES_TABLE_ID"]
$Password = $config["CLIENT_MASTER_PASSWORD"]
$MasterPath = if ($config.ContainsKey("MASTER_EXCEL_PATH") -and $config["MASTER_EXCEL_PATH"]) { $config["MASTER_EXCEL_PATH"] } else { $DefaultMasterPath }

if (-not $BaseUrl -or -not $Token -or -not $CompaniesTableId -or -not $Password) {
    throw "Missing required NocoDB or Master Excel configuration"
}

$headers = @{ "xc-token" = $Token; "Content-Type" = "application/json" }

if (-not $DiagnosticMinimal) {
    $metadata = Invoke-NocoGet -Url "$BaseUrl/api/v2/meta/tables/$CompaniesTableId" -Headers $headers
    $existingFields = @($metadata.columns | ForEach-Object { $_.title })
    $missingFields = @($PhaseFields | Where-Object { $existingFields -notcontains $_ })
    if ($missingFields.Count -gt 0) {
        throw "Missing Phase 1 Companies fields: $($missingFields -join ', ')"
    }
}

$companies = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers

if (-not $DiagnosticMinimal -and -not $DiagnosticArray -and -not $DiagnosticFieldIsolation) {
    $excelData = Read-MasterRows -MasterPath $MasterPath -Password $Password
} else {
    $excelData = @{ Rows = @(); UsedRange = "N/A (diagnostic mode)" }
}

$companiesByLegal = @{}
$duplicateCompanyKeys = @()
foreach ($company in $companies) {
    $key = Normalize-Key $company."Legal Name"
    if (-not $key) { continue }
    if (-not $companiesByLegal.ContainsKey($key)) {
        $companiesByLegal[$key] = @()
    }
    $companiesByLegal[$key] += $company
}
foreach ($key in $companiesByLegal.Keys) {
    if (@($companiesByLegal[$key]).Count -gt 1) {
        $duplicateCompanyKeys += [pscustomobject]@{
            normalized_legal_name = $key
            count = @($companiesByLegal[$key]).Count
        }
    }
}

$plannedUpdates = @()
$unmatchedRows = @()
$conflicts = @()
$noChangeRows = @()
$now = Get-Date
$syncStamp = $now.ToString("yyyy-MM-dd HH:mm:ss")

foreach ($row in $excelData.Rows) {
    $legalName = Normalize-Text $row."Legal Name"
    if (-not $legalName) { continue }
    $key = Normalize-Key $legalName
    if (-not $companiesByLegal.ContainsKey($key)) {
        $unmatchedRows += [pscustomobject]@{ source_row = $row.__row; legal_name = $legalName; reason = "No matching NocoDB Companies row by Legal Name" }
        continue
    }
    $matches = @($companiesByLegal[$key])
    if ($matches.Count -ne 1) {
        $unmatchedRows += [pscustomobject]@{ source_row = $row.__row; legal_name = $legalName; reason = "Multiple matching NocoDB Companies rows by Legal Name" }
        continue
    }

    $company = $matches[0]
    $update = [ordered]@{}
    $reasons = New-Object System.Collections.Generic.List[string]

    foreach ($target in $DateTargets) {
        $source = $ExcelToTarget[$target]
        $parsed = Convert-ToIsoDate -RawValue $row.$source -FieldName $target
        if ($parsed.Value) {
            $update[$target] = $parsed.Value
        } else {
            $update[$target] = $null
            Add-UniqueReason -Reasons $reasons -Reason $parsed.Reason
        }
    }

    $monthParsed = Convert-ToMonth -RawValue $row."Annual Return"
    if ($monthParsed.Value) {
        $update["Annual Return Month"] = $monthParsed.Value
    } else {
        $update["Annual Return Month"] = $null
        Add-UniqueReason -Reasons $reasons -Reason $monthParsed.Reason
    }

    $update["Workbench Needs Review"] = ($reasons.Count -gt 0)
    $update["Workbench Review Reason"] = if ($reasons.Count -gt 0) { ($reasons -join "; ") } else { "" }
    $update["Workbench Last Updated At"] = $syncStamp

    $payload = [ordered]@{}
    $rowConflicts = @()
    foreach ($field in $update.Keys) {
        $newValue = $update[$field]
        $current = $company.$field
        $currentText = Normalize-Text $current
        $newText = Normalize-Text $newValue
        $currentIsEmpty = Is-CurrentValueEmptyForField -FieldName $field -Value $current
        $isManagedWorkbenchField = $ManagedWorkbenchFields -contains $field
        if ((-not $isManagedWorkbenchField) -and (-not $currentIsEmpty) -and $currentText -ne $newText) {
            $rowConflicts += [pscustomobject]@{
                field = $field
                current = if ($field -match "Email|Phone") { "[masked]" } else { $currentText }
                proposed = $newText
            }
            continue
        }
        if (($isManagedWorkbenchField -or $currentIsEmpty) -and $null -ne $newValue -and $newText -ne "" -and $currentText -ne $newText) {
            $payload[$field] = $newValue
        }
    }

    if ($rowConflicts.Count -gt 0) {
        $conflicts += [pscustomobject]@{
            source_row = $row.__row
            company_id = $company.Id
            legal_name = $legalName
            conflicts = $rowConflicts
        }
    }

    if ($payload.Count -gt 0) {
        $plannedUpdates += [pscustomobject]@{
            source_row = $row.__row
            company_id = $company.Id
            legal_name = $legalName
            fields = $payload
            review_reason = $update["Workbench Review Reason"]
        }
    } elseif ($rowConflicts.Count -eq 0) {
        $noChangeRows += [pscustomobject]@{ source_row = $row.__row; company_id = $company.Id; legal_name = $legalName }
    }
}

$applyResults = @()
$applyFailures = @()
$diagnosticResult = $null

if ($DiagnosticMinimal) {
    $targetId = 1
    $diagnosticValue = "Bookkeeping Filed Until is blank or marked NA"
    $body = [ordered]@{
        Id = $targetId
        "Workbench Review Reason" = $diagnosticValue
    }
    $endpoint = "$BaseUrl/api/v2/tables/$CompaniesTableId/records"

    $beforeRow = @($companies | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
    $legalName = if ($beforeRow) { $beforeRow."Legal Name" } else { "Unknown" }

    $diagnosticPayloadPath = Join-Path $ReportDir "diagnostic_payload.json"
    $body | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $diagnosticPayloadPath -Encoding UTF8

    $diagnosticResult = [ordered]@{
        endpoint = "/api/v2/tables/{CompaniesTableId}/records"
        payload_shape = "single object"
        target_company_id = $targetId
        target_legal_name = $legalName
        payload_fields = @("Id", "Workbench Review Reason")
        diagnostic_value = $diagnosticValue
        payload_path = $diagnosticPayloadPath
        http_status = $null
        response_body = ""
        patch_succeeded = $false
        workbench_review_reason_changed = $false
        before_value = if ($beforeRow) { $beforeRow."Workbench Review Reason" } else { $null }
        after_value = $null
    }

    try {
        $jsonSent = $body | ConvertTo-Json -Depth 8
        $jsonSentPath = Join-Path $ReportDir "diagnostic_payload_array_actual.json"
        $jsonSent | Set-Content -LiteralPath $jsonSentPath -Encoding UTF8

        $null = Invoke-NocoPatch -Url $endpoint -Headers $headers -Body $body -ContentType "application/json; charset=utf-8"
        $diagnosticResult.http_status = 200
        $diagnosticResult.patch_succeeded = $true

        $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
        $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
        $diagnosticResult.after_value = if ($afterRow) { $afterRow."Workbench Review Reason" } else { $null }

        $beforeChanged = (Normalize-Text $diagnosticResult.before_value) -ne (Normalize-Text $diagnosticResult.after_value)
        $diagnosticResult.workbench_review_reason_changed = $beforeChanged
        if ($beforeChanged) {
            $diagnosticResult.patch_succeeded = $true
        }
    } catch {
        $details = Get-HttpErrorDetails -ErrorRecord $_
        $diagnosticResult.http_status = $details.status
        $diagnosticResult.response_body = $details.response_body

        $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
        $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
        $diagnosticResult.after_value = if ($afterRow) { $afterRow."Workbench Review Reason" } else { $null }

        $beforeChanged = (Normalize-Text $diagnosticResult.before_value) -ne (Normalize-Text $diagnosticResult.after_value)
        $diagnosticResult.workbench_review_reason_changed = $beforeChanged
    }
}

if ($DiagnosticArray) {
    $targetId = 1
    $diagnosticValue = "Bookkeeping Filed Until is blank or marked NA"
    $record = [ordered]@{
        Id = $targetId
        "Workbench Review Reason" = $diagnosticValue
    }
    $body = @(,$record)
    $endpoint = "$BaseUrl/api/v2/tables/$CompaniesTableId/records"

    $beforeRow = @($companies | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
    $legalName = if ($beforeRow) { $beforeRow."Legal Name" } else { "Unknown" }

    $diagnosticPayloadPath = Join-Path $ReportDir "diagnostic_payload_array.json"
    $body | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $diagnosticPayloadPath -Encoding UTF8

    $diagnosticResult = [ordered]@{
        endpoint = "/api/v2/tables/{CompaniesTableId}/records"
        payload_shape = "array with one object"
        target_company_id = $targetId
        target_legal_name = $legalName
        payload_fields = @("Id", "Workbench Review Reason")
        diagnostic_value = $diagnosticValue
        payload_path = $diagnosticPayloadPath
        http_status = $null
        response_body = ""
        patch_succeeded = $false
        workbench_review_reason_changed = $false
        before_value = if ($beforeRow) { $beforeRow."Workbench Review Reason" } else { $null }
        after_value = $null
    }

    try {
        $null = Invoke-NocoPatch -Url $endpoint -Headers $headers -Body $body -ContentType "application/json; charset=utf-8"
        $diagnosticResult.http_status = 200
        $diagnosticResult.patch_succeeded = $true

        $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
        $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
        $diagnosticResult.after_value = if ($afterRow) { $afterRow."Workbench Review Reason" } else { $null }

        $beforeChanged = (Normalize-Text $diagnosticResult.before_value) -ne (Normalize-Text $diagnosticResult.after_value)
        $diagnosticResult.workbench_review_reason_changed = $beforeChanged
        if ($beforeChanged) {
            $diagnosticResult.patch_succeeded = $true
        }
    } catch {
        $details = Get-HttpErrorDetails -ErrorRecord $_
        $diagnosticResult.http_status = $details.status
        $diagnosticResult.response_body = $details.response_body

        $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
        $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
        $diagnosticResult.after_value = if ($afterRow) { $afterRow."Workbench Review Reason" } else { $null }

        $beforeChanged = (Normalize-Text $diagnosticResult.before_value) -ne (Normalize-Text $diagnosticResult.after_value)
        $diagnosticResult.workbench_review_reason_changed = $beforeChanged
    }
}

if ($DiagnosticFieldIsolation) {
    $targetId = 1
    $endpoint = "$BaseUrl/api/v2/tables/$CompaniesTableId/records"

    $beforeRow = @($companies | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
    $legalName = if ($beforeRow) { $beforeRow."Legal Name" } else { "Unknown" }

    $fieldTests = @(
        @{ Field = "Workbench Needs Review"; Value = $true },
        @{ Field = "GST Filed Until"; Value = "2026-03-30" },
        @{ Field = "GST Due Next Period"; Value = "2026-07-30" },
        @{ Field = "PST Filed Until"; Value = "2025-12-31" },
        @{ Field = "PST Due Next Period"; Value = "2027-01-31" },
        @{ Field = "T2 Filed Until"; Value = "2024-12-31" },
        @{ Field = "T2 Due Next Period"; Value = "2025-12-31" }
    )

    $fieldResults = @()
    $firstFailingField = $null
    $allSucceeded = $true

    foreach ($test in $fieldTests) {
        $fieldName = $test.Field
        $fieldValue = $test.Value

        $body = [ordered]@{
            Id = $targetId
            $fieldName = $fieldValue
        }

        $payloadFileName = "diagnostic_field_$($fieldName -replace ' ', '_')_payload.json"
        $diagnosticPayloadPath = Join-Path $ReportDir $payloadFileName
        $body | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $diagnosticPayloadPath -Encoding UTF8

        $beforeValue = $beforeRow.$fieldName
        $httpStatus = $null
        $responseBody = ""
        $patchSucceeded = $false
        $afterValue = $null
        $fieldChanged = $false

        try {
            $null = Invoke-NocoPatch -Url $endpoint -Headers $headers -Body $body -ContentType "application/json; charset=utf-8"
            $httpStatus = 200
            $patchSucceeded = $true

            $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
            $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
            $afterValue = $afterRow.$fieldName

            $beforeChanged = (Normalize-Text $beforeValue) -ne (Normalize-Text $afterValue)
            $fieldChanged = $beforeChanged

            $fieldResults += [pscustomobject]@{
                field_name = $fieldName
                payload_path = $diagnosticPayloadPath
                http_status = $httpStatus
                response_body = $responseBody
                before_value = $beforeValue
                after_value = $afterValue
                changed = $fieldChanged
                succeeded = $true
            }
        } catch {
            $details = Get-HttpErrorDetails -ErrorRecord $_
            $httpStatus = $details.status
            $responseBody = $details.response_body
            $patchSucceeded = $false

            $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
            $afterRow = @($afterRows | Where-Object { $_.Id -eq $targetId } | Select-Object -First 1)[0]
            $afterValue = $afterRow.$fieldName

            $beforeChanged = (Normalize-Text $beforeValue) -ne (Normalize-Text $afterValue)
            $fieldChanged = $beforeChanged

            $fieldResults += [pscustomobject]@{
                field_name = $fieldName
                payload_path = $diagnosticPayloadPath
                http_status = $httpStatus
                response_body = $responseBody
                before_value = $beforeValue
                after_value = $afterValue
                changed = $fieldChanged
                succeeded = $false
            }
            if ($fieldName -eq "Workbench Needs Review") {
                $beforeRow = $afterRow
                continue
            }
            $allSucceeded = $false
            $firstFailingField = $fieldName
            break
        }

        $beforeRow = $afterRow
    }

    $diagnosticResult = [ordered]@{
        endpoint = "/api/v2/tables/{CompaniesTableId}/records"
        diagnostic_mode = "field-isolation"
        target_company_id = $targetId
        target_legal_name = $legalName
        field_attempts = $fieldResults
        first_failing_field = $firstFailingField
        all_fields_succeeded = $allSucceeded
    }
}

if ($DiagnosticOne -and -not $Apply) {
    throw "-DiagnosticOne requires -Apply so exactly one guarded PATCH can be attempted."
}
if ($Apply) {
    $itemsToApply = if ($DiagnosticOne) { @($plannedUpdates | Select-Object -First 1) } else { $plannedUpdates }
    foreach ($item in $itemsToApply) {
        $record = [ordered]@{ Id = $item.company_id }
        if ($item.fields -is [System.Collections.IDictionary]) {
            foreach ($entry in $item.fields.GetEnumerator()) {
                $record[$entry.Key] = $entry.Value
            }
        } else {
            foreach ($property in $item.fields.PSObject.Properties) {
                $record[$property.Name] = $property.Value
            }
        }
        $body = $record
        $endpoint = "$BaseUrl/api/v2/tables/$CompaniesTableId/records"
        $fieldNames = @($record.Keys | Where-Object { $_ -ne "Id" })
        $beforeRow = @($companies | Where-Object { $_.Id -eq $item.company_id } | Select-Object -First 1)[0]
        if ($DiagnosticOne) {
            $diagnosticResult = [ordered]@{
                endpoint = "/api/v2/tables/{CompaniesTableId}/records"
                payload_shape = "single object containing Id plus field/value pairs"
                target_company_id = $item.company_id
                target_legal_name = $item.legal_name
                field_names = $fieldNames
                http_status = $null
                response_body = ""
                patch_succeeded = $false
                any_field_changed = $false
                changed_fields = @()
                report_note = "Single-row diagnostic guard was active; no batch apply was attempted."
            }
        }
        try {
            $null = Invoke-NocoPatch -Url $endpoint -Headers $headers -Body $body
            $applyResults += [pscustomobject]@{ company_id = $item.company_id; legal_name = $item.legal_name; field_count = $item.fields.Count }
            if ($DiagnosticOne) {
                $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
                $afterRow = @($afterRows | Where-Object { $_.Id -eq $item.company_id } | Select-Object -First 1)[0]
                $changed = @()
                foreach ($fieldName in $fieldNames) {
                    if ((Normalize-Text $beforeRow.$fieldName) -ne (Normalize-Text $afterRow.$fieldName)) {
                        $changed += $fieldName
                    }
                }
                $diagnosticResult.http_status = 200
                $diagnosticResult.patch_succeeded = $true
                $diagnosticResult.any_field_changed = ($changed.Count -gt 0)
                $diagnosticResult.changed_fields = $changed
            }
        } catch {
            $details = Get-HttpErrorDetails -ErrorRecord $_
            $applyFailures += [pscustomobject]@{
                company_id = $item.company_id
                legal_name = $item.legal_name
                error = $_.Exception.Message
                http_status = $details.status
                response_body = $details.response_body
            }
            if ($DiagnosticOne) {
                $afterRows = Get-AllNocoRows -BaseUrl $BaseUrl -TableId $CompaniesTableId -Headers $headers
                $afterRow = @($afterRows | Where-Object { $_.Id -eq $item.company_id } | Select-Object -First 1)[0]
                $changed = @()
                foreach ($fieldName in $fieldNames) {
                    if ((Normalize-Text $beforeRow.$fieldName) -ne (Normalize-Text $afterRow.$fieldName)) {
                        $changed += $fieldName
                    }
                }
                $diagnosticResult.http_status = $details.status
                $diagnosticResult.response_body = $details.response_body
                $diagnosticResult.any_field_changed = ($changed.Count -gt 0)
                $diagnosticResult.changed_fields = $changed
            }
            if ($DiagnosticOne) {
                break
            }
        }
        if ($DiagnosticOne) {
            break
        }
    }
}

$summary = [ordered]@{
    generated_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    mode = if ($Apply) { "apply" } elseif ($DiagnosticMinimal) { "diagnostic-minimal" } elseif ($DiagnosticArray) { "diagnostic-array" } elseif ($DiagnosticFieldIsolation) { "diagnostic-field-isolation" } else { "dry-run" }
    master_path = $MasterPath
    master_used_range = $excelData.UsedRange
    excel_rows = @($excelData.Rows).Count
    nocodb_companies_rows = @($companies).Count
    phase_fields_present = $true
    diagnostic_one = [bool]$DiagnosticOne
    diagnostic_minimal = [bool]$DiagnosticMinimal
    diagnostic_array = [bool]$DiagnosticArray
    diagnostic_field_isolation = [bool]$DiagnosticFieldIsolation
    duplicate_nocodb_legal_name_groups = @($duplicateCompanyKeys).Count
    unmatched_excel_rows = @($unmatchedRows).Count
    conflict_rows = @($conflicts).Count
    planned_update_rows = @($plannedUpdates).Count
    no_change_rows = @($noChangeRows).Count
    applied_rows = @($applyResults).Count
    apply_failures = @($applyFailures).Count
}

$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "summary.json") -Encoding UTF8
ConvertTo-Json -InputObject @($plannedUpdates) -Depth 10 | Set-Content -LiteralPath (Join-Path $ReportDir "planned_updates.json") -Encoding UTF8
ConvertTo-Json -InputObject @($unmatchedRows) -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "unmatched_excel_rows.json") -Encoding UTF8
ConvertTo-Json -InputObject @($conflicts) -Depth 10 | Set-Content -LiteralPath (Join-Path $ReportDir "conflicts_existing_non_empty.json") -Encoding UTF8
ConvertTo-Json -InputObject @($duplicateCompanyKeys) -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "duplicate_company_keys.json") -Encoding UTF8
ConvertTo-Json -InputObject @($noChangeRows) -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "no_change_rows.json") -Encoding UTF8
ConvertTo-Json -InputObject @($applyResults) -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "apply_results.json") -Encoding UTF8
ConvertTo-Json -InputObject @($applyFailures) -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "apply_failures.json") -Encoding UTF8
if ($DiagnosticOne -or $DiagnosticMinimal -or $DiagnosticArray -or $DiagnosticFieldIsolation) {
    $diagnosticResult | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDir "diagnostic_patch_result.json") -Encoding UTF8
}

$summaryMd = @"
# Phase 1 Client Workbench Sync Report

| Item | Value |
| --- | ---: |
| Mode | $($summary.mode) |
| Excel rows | $($summary.excel_rows) |
| NocoDB Companies rows | $($summary.nocodb_companies_rows) |
| Planned update rows | $($summary.planned_update_rows) |
| Conflict rows | $($summary.conflict_rows) |
| Unmatched Excel rows | $($summary.unmatched_excel_rows) |
| Duplicate NocoDB Legal Name groups | $($summary.duplicate_nocodb_legal_name_groups) |
| Applied rows | $($summary.applied_rows) |
| Apply failures | $($summary.apply_failures) |

Safety rules:

- Master Excel is read-only.
- Existing non-empty target fields are not overwritten.
- No Engagement, Events, email, CRA, invoice, or outbound action.
- Calculated due fields are not populated in Phase 1 until rules are confirmed.
"@
$summaryMd | Set-Content -LiteralPath (Join-Path $ReportDir "summary.md") -Encoding UTF8

[pscustomobject]@{
    mode = $summary.mode
    report_dir = $ReportDir
    planned_update_rows = $summary.planned_update_rows
    conflict_rows = $summary.conflict_rows
    unmatched_excel_rows = $summary.unmatched_excel_rows
    apply_failures = $summary.apply_failures
} | ConvertTo-Json -Depth 5
