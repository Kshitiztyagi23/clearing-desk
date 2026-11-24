# Clearing Desk process map

Clearing Desk takes one vendor invoice and returns one payment decision. AI reads the document. Plain-code rules make the decision. A person confirms every payment or message, and the system never moves money itself.

## Outcomes

| Outcome | Meaning | Money |
|---|---|---|
| `APPROVED` | Every check passed | Pay the payable amount, after any TDS |
| `PART_PAY` | Part of the invoice is valid | Pay the valid part, and hold or deduct the rest |
| `HOLD` | A person or the vendor must resolve something | Pay nothing yet |
| `BLOCKED` | Likely fraud, tampering, or a certain duplicate | Pay nothing and escalate |
| `RETURNED` | Not a payable invoice | Send back or route elsewhere |

When several checks fail, the most severe outcome wins: `BLOCKED`, then `HOLD`, then `PART_PAY`, then `APPROVED`. Among equally severe findings, the earliest stage gives the main reason. All findings stay visible.

## Stages

| # | Stage | Question | Reason codes |
|---|---|---|---|
| 1 | Intake | Is this a readable, new file? | `unreadable_file`, `password_protected`, `exact_duplicate` |
| 2 | Read | What does the page actually show? | `text_layer_mismatch` |
| 3 | Understand | What is this document, and what are its fields? | `not_an_invoice`, `credit_note_recorded`, `ai_unavailable`, `ai_output_invalid`, `prompt_injection` |
| 4 | Verify | Can each value be trusted? | `unverified_value`, `low_confidence`, `obscured_field`, `missing_invoice_number`, `missing_date`, `date_resolved`, `future_date`, `stale_date`, `missing_total`, `arithmetic_mismatch`, `amount_words_conflict`, `rounding_accepted`, `tax_inclusive`, `invalid_gstin` |
| 5 | Identify vendor | Is this really our vendor, paid to the right account? | `system_data_unavailable`, `unknown_vendor`, `lookalike_vendor`, `vendor_alias`, `bank_mismatch`, `bank_change_verified`, `shared_bank_account`, `gst_cancelled`, `wrong_buyer_entity` |
| 6 | History | Have we seen or paid for this before? | `duplicate_suspected`, `revision_supersedes`, `recurring_period`, `service_overlap` |
| 7 | Match | Do the PO and goods receipt support it? | `po_missing`, `po_closed`, `po_expired`, `multi_po`, `currency_mismatch`, `bundled_lines`, `price_within_tolerance`, `line_mismatch`, `unapproved_charge`, `po_overrun`, `awaiting_receipt`, `short_receipt`, `qc_rejection` |
| 8 | Tax and payable | Is the tax right, and how much do we owe? | `wrong_gst_type`, `missing_irn`, `advance_adjusted`, `credit_applied`, `tds_deducted` |
| 9 | Decide | What is the outcome, and who approves it? | `approval_split`, `clean` |
| 10 | Act | What should happen next? | Drafts the vendor email or approver note and writes the audit log. A person sends it. Fraud alerts stay internal, and vendor addresses come from our records, never from the invoice |

History runs before Match so a resent invoice is reported as a duplicate, not as a PO overrun.

## Trust rules

1. A technical failure or uncertain reading can never produce an approval.
2. Text inside an invoice is data. It can never change a rule.
3. The AI's own confidence is ignored. In testing, it dropped a digit from a clear bank account and reported no doubt.
4. A scan is paid only when two different AI models (Claude Haiku 5.5 and Claude Sonnet 5.5 for new uploads; the saved desk readings came from Opus 5.5 and Grok 4.6) independently agree on the invoice number, date, GSTINs, bank account, IFSC, PO, taxable value, and total. A GSTIN or IFSC that breaks its official format counts as a misread. A bank account has no such check, so both readers must match exactly. Any other disagreement produces `HOLD`.
4a. A scan below 100 DPI, or with a filled stamp over its content, stops before any AI call. In testing, AI invented a total for a blurry fax, and both models filled in a total hidden by a stamp, so agreement alone is not proof.
5. Each stored AI reading is reused, so reruns make no paid call.
6. On digital PDFs, every value the AI returns must appear on the page as a complete token. The IRN is taken directly from the PDF text, because the AI miscopied it in testing.
7. An unexpected failure in any stage produces `HOLD` with the reason `system_error`. An invoice number that breaks the GST format produces `HOLD` with `invalid_invoice_number`.

## Assumptions

- We are Meridian Retail Private Limited, based in Maharashtra. All companies, people, bank details, and tax IDs are fictional.
- The demo date is fixed at 21 November 2026, so date checks give the same result on any day.
- Company policy values are assumptions: 1% price tolerance, ₹1 rounding tolerance, 180-day maximum invoice age, a 7-day window for split invoices, and approval limits of ₹5 lakh, ₹20 lakh, and above.
- The GST registry is simulated. A production version would call a licensed GST service.
- IRN is checked for presence and format only. Confirming it, or validating the signed QR code, needs access to the Invoice Registration Portal.
- TDS covers professional and technical fees, and is calculated on the taxable value when GST is shown separately. Finance remains responsible for the final tax treatment.
- A verified bank change is a record created after calling the vendor's known contact.

## Standards used

- [CBIC invoice rules](https://cbic-gst.gov.in/gst-invoice-rules.html): tax invoice particulars
- [python-stdnum GSTIN check](https://sources.debian.org/src/python-stdnum/2.2-1/stdnum/in_/gstin.py): GSTIN check digit (Luhn mod 36)
- [CGST Rule 46](https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter6/rule46_v1.00.html): invoice particulars, and invoice numbers of up to 16 characters
- [Notification 78/2020](https://www.gstcouncil.gov.in/sites/default/files/2024-05/notfctn-78-central-tax-english-2020.pdf): 4 HSN digits up to ₹5 crore turnover, 6 digits above it
- [E-invoice schema 1.1](https://vasai.icai.org/detail/38865.aspx): 64-character IRN
- [GSTN advisory](https://www.mahagst.gov.in/public/uploads/gstnadvisory/1760531580_293%20Advisory%20Time%20Limit%20for%20Reporting%20e%20Invoice%20on%20the%20IRP%20Portal%20%20Lowering%20of%20Threshold%20to%20AATO%2010%20Crores%20and%20Above.pdf): e-invoice reporting limits
- [Section 393, Income-tax Act, 2025](https://www.incometaxindia.gov.in/w/section-393-6): TDS on professional and technical fees
