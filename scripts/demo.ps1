$ErrorActionPreference = "Stop"
$api = "http://localhost:8000"
$fixture = Join-Path $PSScriptRoot "..\evaluation\fixtures\po-1001-invoice.json"
$ingested = Invoke-RestMethod -Method Post -Uri "$api/api/v1/ingest-invoice" -Form @{ file = Get-Item $fixture }
$invoiceId = $ingested.invoice.id
$match = Invoke-RestMethod -Method Post -Uri "$api/api/v1/match-po" -ContentType "application/json" -Body (@{ invoice_id = $invoiceId; require_goods_receipt = $true } | ConvertTo-Json)
$journal = Invoke-RestMethod -Method Post -Uri "$api/api/v1/post-payment-journal" -Headers @{ "Idempotency-Key" = "demo-$invoiceId" } -ContentType "application/json" -Body (@{ invoice_id = $invoiceId } | ConvertTo-Json)
$audit = Invoke-RestMethod -Method Get -Uri "$api/api/v1/audit-log?entity_id=$invoiceId"
[pscustomobject]@{ InvoiceId = $invoiceId; Matched = $match.result.matched; JournalId = $journal.journal_id; AuditEvents = $audit.events.Count; ChainValid = $audit.chain_valid } | Format-List

