$ErrorActionPreference = "Stop"
$api = "http://localhost:8000"
$fixture = Join-Path $PSScriptRoot "..\evaluation\fixtures\po-1001-invoice.json"
$ingested = Invoke-RestMethod -Method Post -Uri "$api/api/v1/ingest-invoice" -Form @{ file = Get-Item $fixture }
$invoiceId = $ingested.invoice.id
$match = Invoke-RestMethod -Method Post -Uri "$api/api/v1/match-po" -ContentType "application/json" -Body (@{ invoice_id = $invoiceId; require_goods_receipt = $true } | ConvertTo-Json)
if (-not $match.result.matched) {
    throw "The demo invoice did not pass deterministic matching. Resolve the exception before posting."
}
if ($match.next_action -eq "human-review") {
    Invoke-RestMethod -Method Post -Uri "$api/api/v1/exceptions/decision" -ContentType "application/json" -Body (@{
        invoice_id = $invoiceId
        approved = $true
        actor = "reviewer:demo"
        comment = "Approved after reviewing the agent escalation and deterministic evidence"
    } | ConvertTo-Json) | Out-Null
}
$idempotencyKey = if ($match.next_action -eq "posted") { "auto:$invoiceId" } else { "demo:$invoiceId" }
$journal = Invoke-RestMethod -Method Post -Uri "$api/api/v1/post-payment-journal" -Headers @{ "Idempotency-Key" = $idempotencyKey } -ContentType "application/json" -Body (@{ invoice_id = $invoiceId } | ConvertTo-Json)
$retry = Invoke-RestMethod -Method Post -Uri "$api/api/v1/post-payment-journal" -Headers @{ "Idempotency-Key" = $idempotencyKey } -ContentType "application/json" -Body (@{ invoice_id = $invoiceId } | ConvertTo-Json)
$audit = Invoke-RestMethod -Method Get -Uri "$api/api/v1/audit-log?entity_id=$invoiceId"
$remittanceFixture = Join-Path $PSScriptRoot "..\evaluation\fixtures\remittance-clean.json"
$ar = Invoke-RestMethod -Method Post -Uri "$api/api/v1/ingest-document" -Form @{ file = Get-Item $remittanceFixture }
$arAudit = Invoke-RestMethod -Method Get -Uri "$api/api/v1/audit-log?entity_id=$($ar.entity_id)"
[pscustomobject]@{
    APInvoiceId = $invoiceId
    APMatched = $match.result.matched
    APJournalId = $journal.journal_id
    APReplayReturnedSameJournal = $journal.journal_id -eq $retry.journal_id
    ARRemittanceId = $ar.entity_id
    ARMatched = $ar.result.matched
    CashApplied = $ar.result.applied
    APChainValid = $audit.chain_valid
    ARChainValid = $arAudit.chain_valid
} | Format-List
