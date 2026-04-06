#!/usr/bin/env python3
"""
Business Loan Matcher
=====================
Fill in loan_intake_template.json, then run:
    python business_loan_matcher.py

To create a fresh blank template:
    python business_loan_matcher.py --init
"""

import json
import csv
import sys
import io
import contextlib
from pathlib import Path
from datetime import datetime

# Windows: force UTF-8 so emoji render correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR   = Path(__file__).resolve().parent
TEMPLATE_FILE = SCRIPT_DIR / "loan_intake_template.json"
OUTPUT_DIR   = SCRIPT_DIR / "loan_results"
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── LOAN PRODUCTS ────────────────────────────────────────────────────────────

LOAN_PRODUCTS = [
    {
        "name": "SBA 7(a) Loan",
        "type": "SBA",
        "max_amount": 5_000_000,
        "min_amount": 5_000,
        "apr_range": "6.5% – 10%",
        "term_range": "Up to 10 yrs (25 for real estate)",
        "requirements": {
            "min_months_in_business": 24,
            "min_personal_credit": 650,
            "min_annual_revenue": 100_000,
            "no_tax_liens": True,
            "no_judgments": True,
        },
        "purposes": ["working_capital", "equipment", "real_estate", "expansion", "debt_refinance", "inventory", "other"],
        "notes": "Best rates available. Slow approval (weeks). SBA guarantee reduces lender risk.",
    },
    {
        "name": "SBA 504 Loan",
        "type": "SBA",
        "max_amount": 5_500_000,
        "min_amount": 125_000,
        "apr_range": "5.5% – 7.5%",
        "term_range": "10 or 20 years",
        "requirements": {
            "min_months_in_business": 24,
            "min_personal_credit": 680,
            "min_annual_revenue": 250_000,
            "no_tax_liens": True,
            "no_judgments": True,
        },
        "purposes": ["real_estate", "equipment"],
        "notes": "Only for real estate or major equipment. Requires ~10% down payment.",
    },
    {
        "name": "SBA Microloan",
        "type": "SBA",
        "max_amount": 50_000,
        "min_amount": 500,
        "apr_range": "8% – 13%",
        "term_range": "Up to 6 years",
        "requirements": {
            "min_months_in_business": 6,
            "min_personal_credit": 575,
            "min_annual_revenue": 0,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital", "equipment", "inventory", "expansion", "other"],
        "notes": "Great for startups and small needs. Issued through nonprofit intermediaries.",
    },
    {
        "name": "Traditional Bank Term Loan",
        "type": "Bank",
        "max_amount": 1_000_000,
        "min_amount": 25_000,
        "apr_range": "6% – 13%",
        "term_range": "1 – 7 years",
        "requirements": {
            "min_months_in_business": 24,
            "min_personal_credit": 680,
            "min_annual_revenue": 250_000,
            "no_tax_liens": True,
            "no_judgments": True,
        },
        "purposes": ["working_capital", "equipment", "expansion", "debt_refinance", "inventory", "other"],
        "notes": "Low rates, strict qualification. Approval takes weeks. Best for established businesses.",
    },
    {
        "name": "Business Line of Credit",
        "type": "Bank / Online",
        "max_amount": 500_000,
        "min_amount": 10_000,
        "apr_range": "8% – 24%",
        "term_range": "Revolving (renewed annually)",
        "requirements": {
            "min_months_in_business": 12,
            "min_personal_credit": 620,
            "min_annual_revenue": 100_000,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital", "inventory", "other"],
        "notes": "Flexible — draw only what you need. Ideal for managing cash flow gaps.",
    },
    {
        "name": "Online / Alternative Term Loan",
        "type": "Online Lender",
        "max_amount": 500_000,
        "min_amount": 5_000,
        "apr_range": "15% – 45%",
        "term_range": "3 months – 5 years",
        "requirements": {
            "min_months_in_business": 6,
            "min_personal_credit": 580,
            "min_annual_revenue": 100_000,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital", "equipment", "expansion", "inventory", "other"],
        "notes": "Fast approval (1-3 days). Higher rates. Good fallback if bank loans are out of reach.",
    },
    {
        "name": "Invoice Financing",
        "type": "Alternative",
        "max_amount": None,
        "min_amount": 10_000,
        "apr_range": "1% – 5% per month",
        "term_range": "Until invoice paid (30–90 days)",
        "requirements": {
            "min_months_in_business": 3,
            "min_personal_credit": 550,
            "min_annual_revenue": 0,
            "requires_invoices": True,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital"],
        "notes": "Advances 85–90% of outstanding invoices. Credit score is much less important here.",
    },
    {
        "name": "Merchant Cash Advance (MCA)",
        "type": "Alternative",
        "max_amount": 500_000,
        "min_amount": 5_000,
        "apr_range": "40% – 150%+ (factor rate 1.1–1.5)",
        "term_range": "3 – 18 months",
        "requirements": {
            "min_months_in_business": 3,
            "min_personal_credit": 500,
            "min_monthly_cc_sales": 10_000,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital", "inventory", "expansion", "other"],
        "notes": "VERY expensive — last resort only. Repaid as % of daily credit card sales.",
    },
    {
        "name": "Equipment Financing",
        "type": "Bank / Online",
        "max_amount": 5_000_000,
        "min_amount": 5_000,
        "apr_range": "5% – 20%",
        "term_range": "1 – 7 years",
        "requirements": {
            "min_months_in_business": 12,
            "min_personal_credit": 600,
            "min_annual_revenue": 50_000,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["equipment"],
        "notes": "Equipment itself is collateral — easier to qualify. Covers up to 100% of cost.",
    },
    {
        "name": "Revenue-Based Financing",
        "type": "Online Lender",
        "max_amount": 500_000,
        "min_amount": 5_000,
        "apr_range": "15% – 50% (factor rate 1.1–1.3)",
        "term_range": "6 – 24 months",
        "requirements": {
            "min_months_in_business": 6,
            "min_personal_credit": 560,
            "min_monthly_revenue": 10_000,
            "no_tax_liens": False,
            "no_judgments": False,
        },
        "purposes": ["working_capital", "expansion", "inventory", "other"],
        "notes": "Repaid as % of monthly revenue — no fixed payment. Scales with business performance.",
    },
]


# ─── ELIGIBILITY CHECKER ──────────────────────────────────────────────────────

def check_eligibility(loan, data):
    """Returns ('eligible' | 'partial' | 'ineligible', [issues], [warnings])"""
    biz  = data["business"]
    fin  = data["financials"]
    cred = data["credit"]
    req  = data["loan_request"]
    reqs = loan["requirements"]

    issues   = []  # hard fails
    warnings = []  # soft concerns

    months      = biz.get("months_in_business", 0)
    credit      = cred.get("personal_credit_score", 0)
    annual_rev  = fin.get("annual_revenue", 0)
    monthly_rev = fin.get("avg_monthly_revenue", 0)
    monthly_cc  = fin.get("monthly_credit_card_sales", 0)
    monthly_debt = fin.get("monthly_debt_payments", 0)
    has_invoices = fin.get("has_outstanding_invoices", False)
    has_liens   = cred.get("has_tax_liens", False)
    has_judgments = cred.get("has_judgments", False)
    purpose     = req.get("purpose", "other")
    amount      = req.get("amount_needed", 0)

    # Purpose
    if purpose not in loan.get("purposes", []):
        issues.append(f"Loan purpose '{purpose}' not supported by this product")

    # Amount range
    if loan["min_amount"] and amount < loan["min_amount"]:
        issues.append(f"Requested ${amount:,} is below minimum ${loan['min_amount']:,}")
    if loan["max_amount"] and amount > loan["max_amount"]:
        warnings.append(f"Requested ${amount:,} exceeds product max ${loan['max_amount']:,} — partial funding only")

    # Time in business
    min_months = reqs.get("min_months_in_business", 0)
    if months < min_months:
        issues.append(f"Business age {months} mo < required {min_months} mo ({min_months // 12}+ yrs)")

    # Credit score
    min_credit = reqs.get("min_personal_credit", 0)
    if credit < min_credit:
        issues.append(f"Personal credit {credit} < required {min_credit}")
    elif credit < min_credit + 30:
        warnings.append(f"Credit score {credit} is borderline for this product (minimum {min_credit})")

    # Annual revenue
    min_rev = reqs.get("min_annual_revenue", 0)
    if annual_rev < min_rev:
        issues.append(f"Annual revenue ${annual_rev:,} < required ${min_rev:,}")

    # Monthly revenue (revenue-based financing)
    min_monthly = reqs.get("min_monthly_revenue", 0)
    if monthly_rev < min_monthly:
        issues.append(f"Monthly revenue ${monthly_rev:,} < required ${min_monthly:,}")

    # Invoice requirement
    if reqs.get("requires_invoices") and not has_invoices:
        issues.append("Requires outstanding B2B invoices — not indicated")

    # Credit card sales (MCA)
    min_cc = reqs.get("min_monthly_cc_sales", 0)
    if monthly_cc < min_cc:
        issues.append(f"Monthly credit card sales ${monthly_cc:,} < required ${min_cc:,}")

    # Liens / judgments
    if reqs.get("no_tax_liens") and has_liens:
        issues.append("Tax liens on record — disqualifies for this product")
    if reqs.get("no_judgments") and has_judgments:
        issues.append("Active judgments on record — disqualifies for this product")

    # Debt-to-revenue warning
    if monthly_rev > 0 and monthly_debt > 0:
        dtr = monthly_debt / monthly_rev
        if dtr > 0.4:
            warnings.append(f"High monthly debt-to-revenue ratio ({dtr*100:.0f}%) — lenders prefer <40%")

    if issues:
        return "ineligible", issues, warnings
    if warnings:
        return "partial", issues, warnings
    return "eligible", issues, warnings


# ─── REPORT ───────────────────────────────────────────────────────────────────

def print_report(data, results):
    biz  = data["business"]
    fin  = data["financials"]
    cred = data["credit"]
    req  = data["loan_request"]
    sep  = "=" * 72

    print(f"\n{sep}")
    print(f"  BUSINESS LOAN ELIGIBILITY REPORT")
    print(f"  {biz.get('name', 'Unknown Business')}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(sep)
    print(f"\n  SNAPSHOT")
    print(f"  {'Structure:':<22}{biz.get('legal_structure', 'N/A')}")
    print(f"  {'Industry:':<22}{biz.get('industry', 'N/A')}")
    print(f"  {'Time in business:':<22}{biz.get('months_in_business', 0)} months")
    print(f"  {'Annual revenue:':<22}${fin.get('annual_revenue', 0):,}")
    print(f"  {'Avg monthly revenue:':<22}${fin.get('avg_monthly_revenue', 0):,}")
    print(f"  {'Monthly debt payments:':<22}${fin.get('monthly_debt_payments', 0):,}")
    print(f"  {'Personal credit score:':<22}{cred.get('personal_credit_score', 0)}")
    print(f"  {'Tax liens:':<22}{'YES ⚠' if cred.get('has_tax_liens') else 'No'}")
    print(f"  {'Judgments:':<22}{'YES ⚠' if cred.get('has_judgments') else 'No'}")
    print(f"  {'Loan needed:':<22}${req.get('amount_needed', 0):,}  ({req.get('purpose', 'N/A')})")

    eligible   = [(l, r) for l, r in results if r[0] == "eligible"]
    partial    = [(l, r) for l, r in results if r[0] == "partial"]
    ineligible = [(l, r) for l, r in results if r[0] == "ineligible"]

    # ── ELIGIBLE ──
    print(f"\n{sep}")
    print(f"  ELIGIBLE  ✅  ({len(eligible)} products)")
    print(sep)
    if eligible:
        for loan, (_, issues, warnings) in eligible:
            print(f"\n  {loan['name']}  [{loan['type']}]")
            print(f"    Rate:    {loan['apr_range']}")
            print(f"    Term:    {loan['term_range']}")
            if loan["max_amount"]:
                print(f"    Max:     ${loan['max_amount']:,}")
            else:
                print(f"    Max:     Based on invoice value")
            for w in warnings:
                print(f"    ⚠  {w}")
            print(f"    ℹ  {loan['notes']}")
    else:
        print("\n  None at this time.")

    # ── PARTIAL ──
    if partial:
        print(f"\n{sep}")
        print(f"  POSSIBLE — CONCERNS TO ADDRESS  ⚠️  ({len(partial)} products)")
        print(sep)
        for loan, (_, issues, warnings) in partial:
            print(f"\n  {loan['name']}  [{loan['type']}]")
            print(f"    Rate:    {loan['apr_range']}")
            print(f"    Term:    {loan['term_range']}")
            for w in warnings:
                print(f"    ⚠  {w}")
            print(f"    ℹ  {loan['notes']}")

    # ── INELIGIBLE ──
    print(f"\n{sep}")
    print(f"  DOES NOT QUALIFY  ❌  ({len(ineligible)} products)")
    print(sep)
    for loan, (_, issues, warnings) in ineligible:
        print(f"\n  {loan['name']}")
        for i in issues:
            print(f"    ✗  {i}")

    print(f"\n{sep}\n")


# ─── SAVE ─────────────────────────────────────────────────────────────────────

def save_results(data, results):
    biz_name  = data["business"].get("name", "unknown").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base      = OUTPUT_DIR / f"{biz_name}_{timestamp}"

    # TXT
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        print_report(data, results)
    txt_path = base.with_suffix(".txt")
    txt_path.write_text(buffer.getvalue(), encoding="utf-8")

    # CSV
    rows = []
    for loan, (status, issues, warnings) in results:
        rows.append({
            "Business":      data["business"].get("name"),
            "Loan Product":  loan["name"],
            "Type":          loan["type"],
            "Status":        status.upper(),
            "APR Range":     loan["apr_range"],
            "Term":          loan["term_range"],
            "Max Amount":    f"${loan['max_amount']:,}" if loan["max_amount"] else "Invoice-based",
            "Issues":        " | ".join(issues),
            "Warnings":      " | ".join(warnings),
            "Notes":         loan["notes"],
        })
    csv_path = base.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"  📄 Report: {txt_path.name}")
    print(f"  📊 CSV:    {csv_path.name}")
    print(f"  📁 Folder: {OUTPUT_DIR}\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if not TEMPLATE_FILE.exists():
        print(f"\n  Template not found: {TEMPLATE_FILE}")
        print("  Run with --init to create a blank template.\n")
        sys.exit(1)

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    for loan in LOAN_PRODUCTS:
        status, issues, warnings = check_eligibility(loan, data)
        results.append((loan, (status, issues, warnings)))

    order = {"eligible": 0, "partial": 1, "ineligible": 2}
    results.sort(key=lambda x: order[x[1][0]])

    print_report(data, results)
    save_results(data, results)


def create_template():
    template = {
        "_instructions": {
            "legal_structure": "LLC | S-Corp | C-Corp | Sole Proprietor | Partnership",
            "purpose": "working_capital | equipment | real_estate | expansion | debt_refinance | inventory | other",
            "months_in_business": "e.g. 36 = 3 years",
            "personal_credit_score": "approximate: 500, 580, 620, 650, 680, 720, 750",
            "business_credit_score": "0 if unknown"
        },
        "business": {
            "name": "",
            "legal_structure": "",
            "industry": "",
            "state": "",
            "months_in_business": 0,
            "num_employees": 0
        },
        "financials": {
            "annual_revenue": 0,
            "avg_monthly_revenue": 0,
            "monthly_debt_payments": 0,
            "has_outstanding_invoices": False,
            "monthly_credit_card_sales": 0
        },
        "credit": {
            "personal_credit_score": 0,
            "business_credit_score": 0,
            "has_tax_liens": False,
            "has_judgments": False
        },
        "loan_request": {
            "amount_needed": 0,
            "purpose": "working_capital",
            "preferred_term_months": 0,
            "has_collateral": False
        }
    }
    TEMPLATE_FILE.write_text(json.dumps(template, indent=2), encoding="utf-8")
    print(f"\n  ✅ Template created: {TEMPLATE_FILE}")
    print("  Fill it in, then run: python business_loan_matcher.py\n")


if __name__ == "__main__":
    if "--init" in sys.argv:
        create_template()
    else:
        main()
