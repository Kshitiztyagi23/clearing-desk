# Clearing Desk

Clearing Desk takes a vendor invoice PDF and returns an accounts-payable decision: approve, part-pay, hold, or block. Each stage and the evidence behind the decision stay visible.

**Status:** the local app has a Live Desk with 12 ready invoices and PDF upload, a Ledger of every run, and a Test Lab. All 61 test cases pass offline. Deployment is still to come. See [the process map](docs/PROCESS.md).

```powershell
.venv\Scripts\python app.py    # then open http://127.0.0.1:8765
.venv\Scripts\python pipeline.py data\invoices\H01.pdf --fresh --sender billing@kestrelpack.example
.venv\Scripts\python test_pipeline.py
.venv\Scripts\python test_scenarios.py    # every scenario, offline, no AI calls
.venv\Scripts\python tools\build_test_data.py --check
```

## Decisions

| Area | Choice | Reason |
|---|---|---|
| AI | Claude Opus 5.5 on Bedrock for reading invoices only, with Amazon Nova 2 Lite as the backup | Opus read every checked field correctly on a clean invoice and a rotated scan that Nova misread |
| Decisions | Plain-code rules | The same invoice always gets the same, explainable result |
| App | Python, FastAPI, SQLite, plain HTML/CSS/JS | Few moving parts during a live demo |
| PDF | PyMuPDF for text, positions, and page images | One library covers all three |
| Scans | Grok 4.6 on Bedrock as an independent second reader | In testing, one AI dropped a digit from a clear bank account number and did not flag it |
| Hosting | AWS EC2 `t4g.small`, Mumbai, 2 GB RAM, 20 GB disk | Runs on AWS Free plan credits, always on, and keeps run history |

## Data standards

Test vendors are fictional. Invoices follow Indian GST conventions.

**Checked against sources**

- Tax invoice particulars: [CBIC invoice rules](https://cbic-gst.gov.in/gst-invoice-rules.html)
- E-invoice threshold of ₹5 crore and the 30-day reporting limit at ₹10 crore: [GSTN advisory](https://www.mahagst.gov.in/public/uploads/gstnadvisory/1760531580_293%20Advisory%20Time%20Limit%20for%20Reporting%20e%20Invoice%20on%20the%20IRP%20Portal%20%20Lowering%20of%20Threshold%20to%20AATO%2010%20Crores%20and%20Above.pdf)
- GSTIN check digit (Luhn mod 36): [python-stdnum](https://sources.debian.org/src/python-stdnum/2.2-1/stdnum/in_/gstin.py)
- TDS of 10% on professional fees and 2% on technical services, above ₹50,000: [Section 393](https://www.incometaxindia.gov.in/w/section-393-6)
- HSN of 4 digits up to ₹5 crore turnover and 6 digits above it: [Notification 78/2020](https://www.gstcouncil.gov.in/sites/default/files/2024-05/notfctn-78-central-tax-english-2020.pdf)
- Invoice numbers of up to 16 characters: [CGST Rule 46](https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter6/rule46_v1.00.html)
- E-invoice schema 1.1 with a 64-character IRN: [schema text](https://vasai.icai.org/detail/38865.aspx)
