# Clearing Desk

Clearing Desk takes a vendor invoice PDF and returns an accounts-payable decision: approve, part-pay, hold, or block. Each stage and the evidence behind the decision stay visible.

**Status:** project setup. The application is not built yet.

## Decisions

| Area | Choice | Reason |
|---|---|---|
| AI | Amazon Nova 2 Lite on Bedrock for reading invoices only | Read 16 of 17 test fields from a degraded scan in 3.5 seconds |
| Decisions | Plain-code rules | The same invoice always gets the same, explainable result |
| App | Python, FastAPI, SQLite, plain HTML/CSS/JS | Few moving parts during a live demo |
| PDF | PyMuPDF for text, positions, and page images | One library covers all three |
| Scans | Tesseract as a second reader for bank account, GSTIN, IFSC, and totals | In testing, the AI dropped a digit from a clear bank account number and did not flag it |
| Hosting | AWS EC2 `t4g.small`, Mumbai, 2 GB RAM, 20 GB disk | Runs on AWS Free plan credits, always on, and keeps run history |

## Data standards

Test vendors are fictional. Invoices follow Indian GST conventions.

**Checked against sources**

- Tax invoice particulars: [CBIC invoice rules](https://cbic-gst.gov.in/gst-invoice-rules.html)
- E-invoice threshold of ₹5 crore and the 30-day reporting limit at ₹10 crore: [GSTN advisory](https://www.mahagst.gov.in/public/uploads/gstnadvisory/1760531580_293%20Advisory%20Time%20Limit%20for%20Reporting%20e%20Invoice%20on%20the%20IRP%20Portal%20%20Lowering%20of%20Threshold%20to%20AATO%2010%20Crores%20and%20Above.pdf)
- GSTIN check digit (Luhn mod 36): [python-stdnum](https://sources.debian.org/src/python-stdnum/2.2-1/stdnum/in_/gstin.py)
- TDS under Section 393 of the Income-tax Act, 2025: [Income Tax Department](https://www.incometaxindia.gov.in/w/section-393-6)

**To confirm before the matching rule is coded**

- HSN digits required at each turnover level
- Invoice number length limit
- Current e-invoice field list and schema version
- TDS rate and threshold for professional fees
