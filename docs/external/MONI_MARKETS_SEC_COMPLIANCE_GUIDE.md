# MONI MARKETS
## SEC Compliance Guide
### Trading Signals & Auto-Execution Services

**Prepared for:** Elevateo LLC
**Effective Date:** February 2026
**Version:** 1.0

---

## TABLE OF CONTENTS

1. [Executive Summary](#executive-summary)
2. [Stage 1: Signals-Only Service](#stage-1-signals-only-service)
   - Legal Classification
   - Publisher Exemption
   - Permitted Activities
   - Prohibited Activities
   - Required Disclaimers
   - Compliance Costs
3. [Stage 2: Auto-Execution Service](#stage-2-auto-execution-service)
   - Investment Adviser Registration
   - Form ADV Filing
   - Series 65 License
   - Custody Rules
   - Ongoing Obligations
4. [Performance Fee Methodology](#performance-fee-methodology)
   - Qualified Client Verification
   - Fee Structures
   - High-Water Mark Calculation
   - Client Reporting
   - Common Mistakes
5. [Comparison Tables](#comparison-tables)
6. [Implementation Timeline](#implementation-timeline)
7. [Compliance Checklists](#compliance-checklists)
8. [Appendices](#appendices)

---

## EXECUTIVE SUMMARY

### Two Distinct Compliance Paths

**PATH 1: Signals-Only (No SEC Registration)**
- Send trading signals via Discord
- Charge subscription fees ($49-$199/month)
- No investment adviser registration required
- Setup cost: ~$5,000
- Annual cost: ~$2,000

**PATH 2: Auto-Execution (Full SEC Registration)**
- Execute trades on client accounts via Alpaca API
- Charge performance fees (20% of profits)
- Investment adviser registration REQUIRED
- Setup cost: ~$20,000-$30,000
- Annual cost: ~$10,000-$20,000

### Key Decision Point

The critical distinction is **control over client accounts**:
- Providing information = Not regulated
- Executing trades = Fully regulated

---

## STAGE 1: SIGNALS-ONLY SERVICE

### Legal Classification

**Status:** NOT an Investment Adviser
**Exemption:** Publisher Exemption (Investment Advisers Act §202(a)(11)(D))

### Publisher Exemption Requirements

To qualify as exempt publisher, service must be:

1. ✅ **General circulation** - Same signals to all subscribers (not personalized)
2. ✅ **Regular publication** - Consistent schedule (daily/weekly signals)
3. ✅ **Bona fide** - Genuine editorial content, not just buy/sell orders
4. ✅ **Not holding out** - Don't call yourself "investment adviser"

### What You CAN Do (No Registration Required)

| Activity | Compliant | Example |
|----------|-----------|---------|
| Send trading signals | ✅ YES | "Alex bot entered AAPL at $175" |
| Post performance results | ✅ YES | "Alex +11.5% YTD" |
| Explain strategy | ✅ YES | "Uses EMA/RSI/ATR technical analysis" |
| Charge subscriptions | ✅ YES | $49-$199/month tiers |
| General market commentary | ✅ YES | "Markets look bullish today" |
| Educational content | ✅ YES | "How our indicators work" |
| Historical trades | ✅ YES | "Last 30 days: 15 wins, 5 losses" |

### What You CANNOT Do (Would Trigger Registration)

| Activity | Prohibited | Why |
|----------|------------|-----|
| Personalized recommendations | ❌ NO | "YOU should buy AAPL based on YOUR portfolio" |
| Portfolio reviews | ❌ NO | Individual client consultations |
| Tailored advice | ❌ NO | "Based on your risk tolerance..." |
| Execute trades | ❌ NO | Trading on client's behalf |
| Discretionary authority | ❌ NO | Making decisions for client |
| Performance fees | ❌ NO | 20% of client profits |

### Critical Legal Distinction

**INFORMATION vs. ADVICE**

**Information (Not Regulated):**
- "Alex bot is buying AAPL"
- "Here's our technical analysis"
- "Past performance: +11.5%"

**Advice (Regulated):**
- "You should buy AAPL"
- "This fits your risk tolerance"
- "I recommend you allocate 10%"

**The Test:** Is it specific to the individual's circumstances?
- NO = Information (exempt)
- YES = Advice (regulated)

### Required Disclaimers

**Display prominently on:**
- Website homepage
- Terms of Service
- Every Discord channel (pinned)
- Subscription signup page
- Email footer

**Mandatory Language:**

```
═══════════════════════════════════════════════════════════
IMPORTANT DISCLAIMERS

NOT INVESTMENT ADVICE
The information provided is for educational and informational
purposes only. This is NOT personalized investment advice.

NOT REGISTERED
Moni Markets is NOT a registered investment adviser with the
SEC or any state securities regulator.

YOUR RESPONSIBILITY
You are solely responsible for your own investment decisions.
We do NOT manage accounts or execute trades on your behalf.

PAST PERFORMANCE
Past performance is not indicative of future results. Trading
involves substantial risk of loss.

NOT A RECOMMENDATION
Our signals are general market observations. They are NOT
recommendations tailored to your individual circumstances.
═══════════════════════════════════════════════════════════
```

### SEC Enforcement Risk Assessment

**LOW RISK if:**
- ✅ Signals are general (same to all)
- ✅ No individual consultations
- ✅ Clear disclaimers everywhere
- ✅ Don't use "investment adviser" title
- ✅ Don't provide portfolio reviews

**HIGHER RISK if:**
- ⚠️ Personalized advice to individuals
- ⚠️ "Let's discuss YOUR portfolio"
- ⚠️ Acting as fiduciary
- ⚠️ Charging performance fees

**Enforcement Examples:**
- SEC typically targets fraud/misrepresentation
- Unregistered advisers providing personalized advice
- False performance claims
- Pump-and-dump schemes

### No SEC Filing Required

**Stage 1 does NOT require:**
- ❌ Form ADV filing
- ❌ State IA registration
- ❌ Series 65 license
- ❌ Compliance manual
- ❌ Chief Compliance Officer
- ❌ Form CRS
- ❌ E&O insurance (recommended but optional)
- ❌ Annual audits

**Only business requirements:**
- ✅ Wyoming LLC formation
- ✅ Terms of Service with disclaimers
- ✅ Privacy Policy
- ✅ Basic recordkeeping

### Stage 1 Compliance Costs

| Item | One-Time | Annual |
|------|----------|--------|
| Wyoming LLC formation | $250 | - |
| Registered agent | - | $150 |
| US attorney (Terms of Service) | $3,000-$5,000 | - |
| Privacy Policy | $500-$1,000 | - |
| Business accounting | - | $1,500-$2,500 |
| **TOTAL** | **$3,750-$6,250** | **$1,650-$2,650** |

**No ongoing SEC compliance costs**

---

## STAGE 2: AUTO-EXECUTION SERVICE

### Legal Classification

**Status:** Investment Adviser (Regulated Activity)
**Authority:** Investment Advisers Act of 1940

### When IA Registration is REQUIRED

You ARE an Investment Adviser if ALL three apply:

1. ✅ **Provide investment advice** (algorithmic signals executed = advice)
2. ✅ **For compensation** (performance fees or subscription)
3. ✅ **Regular business** (ongoing service, not one-time)

**Your auto-execution model triggers all three:**
- Advice: Bot signals executed on client accounts
- Compensation: 20% performance fees
- Regular business: Monthly subscription service

**Result: MUST register with SEC or State**

### SEC vs. State Registration

| Factor | State Registration | SEC Registration |
|--------|-------------------|------------------|
| **AUM Threshold** | <$100 million | ≥$100 million |
| **Filing Portal** | State securities regulator | SEC (IARD system) |
| **Application Fee** | $200-$500 per state | $1,500 (federal) |
| **Timeline** | 30-90 days | 45-90 days |
| **Annual Fee** | $100-$500/year per state | $1,500/year |
| **Exam Requirement** | Series 65 | Series 65 |

**Your Situation (Starting Out):**
- AUM <$100M = **STATE registration**
- File with Wyoming (home state)
- File with any state where you have 6+ clients

**State-Specific Thresholds:**
- California: 5+ clients in CA
- Texas: 6+ clients in TX
- New York: 6+ clients in NY
- Most states: 6+ clients

### Form ADV Filing Process

**Form ADV Components:**

**Part 1: Business Information**
- Legal name and address (use iPostal1)
- Type of adviser (discretionary)
- Services offered
- Fee schedule (20% performance)
- Assets under management
- Disciplinary history
- Conflicts of interest
- Direct owners and executives

**Part 2A: Brochure (Client-Facing Document)**
- Services and fees
- Types of clients served
- Investment strategies
- Risk factors
- Disciplinary information
- Other business activities
- Code of ethics
- Brokerage practices

**Part 2B: Brochure Supplement**
- Individual advisers (you)
- Educational background
- Business experience (last 5 years)
- Disciplinary information
- Other business activities

**Filing Timeline:**

| Week | Activity |
|------|----------|
| 1-2 | Hire securities attorney ($5K-$10K) |
| 2-4 | Draft Form ADV with attorney |
| 4-5 | Submit to state regulator |
| 5-8 | State review period (30-60 days) |
| 8-9 | Respond to deficiency letters |
| 9-12 | Final approval |

**Total Timeline: 3-4 months**

### Series 65 License Requirement

**Who Needs It:**
- ✅ You (owner/principal)
- ✅ Anyone providing investment advice
- ✅ Anyone with discretionary authority

**Exam Details:**
- **Name:** Uniform Investment Adviser Law Examination
- **Questions:** 130 (10 pretest, don't count)
- **Time:** 180 minutes (3 hours)
- **Passing Score:** 72% (94+ correct out of 130)
- **Cost:** $175 exam fee
- **Study Time:** 60-100 hours

**Content Breakdown:**
- Economic factors and business information (15%)
- Investment vehicle characteristics (25%)
- Client/customer investment recommendations (30%)
- Laws, regulations, ethics (30%)

**Study Resources:**
- Kaplan Financial Education (~$200)
- STC (Securities Training Corporation) (~$250)
- PassPerfect (~$200)

**Testing Locations:**
- Pearson VUE test centers (US only)
- **No international test centers** for Series 65
- Must travel to US to take exam

**Problem for Perpetual Travelers:**
- Must physically be in US for exam
- Plan US trip around test date

**Alternative:**
- Hire US-based licensed IAR (Investment Adviser Representative)
- They hold license, you're beneficial owner
- Cost: $50K-$100K/year for licensed employee

### Custody Rule Compliance

**You Have "Custody" When:**
- ✅ Discretionary authority to trade (via Alpaca API)
- ✅ Access to client funds or securities
- ✅ Ability to withdraw from accounts

**Custody Rule Options:**

**Option 1: Annual Surprise Exam**
- Independent CPA examination
- Unannounced audit of client assets
- Verify account balances
- **Cost:** $5,000-$15,000/year
- **Timing:** Within 6 months of fiscal year-end

**Option 2: Qualified Custodian (RECOMMENDED)**
- Client accounts held at qualified custodian
- Custodian = Alpaca (SEC-registered broker-dealer)
- Custodian sends statements directly to clients
- You never directly hold funds
- **Cost:** $0 (Alpaca handles compliance)

**Your Situation:**
- ✅ Use Alpaca as qualified custodian
- ✅ Clients connect accounts via OAuth
- ✅ You have discretionary authority (API trading)
- ✅ Alpaca sends quarterly statements to clients
- ✅ **No surprise exam needed**

**Required Documentation:**
1. Written agreement with Alpaca (custodian agreement)
2. Client authorization for discretionary trading
3. Quarterly statement delivery confirmation
4. Annual reconciliation of client accounts

### Performance Fee Restrictions

**Rule 205-3: Performance Fees ONLY for "Qualified Clients"**

**Qualified Client Definition (must meet ONE):**

**Option A: Assets Under Management**
- $1,100,000+ invested with YOU immediately after signing

**Option B: Net Worth**
- $2,200,000+ net worth (excluding primary residence)

**This is HIGHER than "Accredited Investor" ($1M net worth)**

**Verification Required:**

For AUM Test:
- ✅ Account opening statement ($1.1M+ deposit)
- ✅ Signed client representation
- ✅ Brokerage statement confirmation

For Net Worth Test:
- ✅ Recent tax return (Form 1040)
- ✅ CPA letter attesting to net worth
- ✅ Bank/brokerage statements (90 days)
- ✅ Property appraisals (real estate)
- ✅ Signed representation form

**Client Representation Form:**

```
QUALIFIED CLIENT REPRESENTATION

I, [Client Name], represent that I meet the definition of
"Qualified Client" under Rule 205-3 because:

☐ I have $1,100,000+ under management with Moni Markets LLC

☐ I have $2,200,000+ net worth (excluding primary residence)

I understand Moni Markets LLC relies on this representation
to charge performance-based compensation.

Signature: ________________  Date: _________
```

**Keep on file for 6 years**

**If Client Doesn't Qualify:**
- ❌ CANNOT charge performance fees
- ✅ Can charge flat monthly fee only ($199-$499)
- ✅ Can provide auto-execution service
- ✅ Wait until client meets threshold

**Violation Penalties:**
- Must return ALL fees collected
- SEC fines: $10,000+ per violation
- Possible suspension from industry

### Required Policies & Procedures

**Compliance Manual Must Include:**

**1. Code of Ethics**
- Personal trading policies (for you/employees)
- Quarterly personal trade reporting
- Pre-clearance procedures
- Restricted securities list
- Gifts and entertainment limits

**2. Best Execution Policy**
- How you ensure best price for clients
- Broker selection criteria (why Alpaca)
- Periodic review of execution quality
- Documentation of broker research

**3. Conflicts of Interest**
- Identification of conflicts
- Management/mitigation strategies
- Disclosure to clients
- Examples:
  - You own the trading software
  - Performance fees incentivize risk

**4. Privacy Policy (Regulation S-P)**
- How client data is collected
- How data is used and shared
- Cybersecurity measures
- Data breach notification procedures
- Client opt-out rights

**5. Business Continuity Plan (BCP)**
- Key personnel backup
- System failure procedures
- Natural disaster response
- Client communication plan
- Document storage/retrieval

**6. Recordkeeping Procedures**
- What records to maintain
- Retention periods (5-6 years)
- Storage methods (cloud-based OK)
- Retrieval procedures
- Annual review process

**7. Marketing & Performance Advertising**
- Performance calculation methodology
- Required disclosures in ads
- Model vs. actual performance
- Cherry-picking prohibition
- Third-party ratings disclosure

**8. Supervision & Review**
- Annual compliance review
- Trade review procedures
- Client complaint handling
- Regulatory exam preparation

**Attorney Cost: $3,000-$8,000 to draft**

### Form CRS (Client Relationship Summary)

**Required Since June 2020**

**What It Is:**
- 2-page plain-English document
- Explains services, fees, conflicts
- Standardized format (SEC template)
- Delivered to ALL retail clients

**Required Content:**

**Section 1: Services**
"What investment services and advice can you provide me?"
- Description of algorithmic trading
- Discretionary authority explanation
- Account monitoring frequency
- Account minimums

**Section 2: Fees**
"What fees will I pay?"
- Performance fee structure (20%)
- How fees are calculated
- High-water mark explanation
- Other costs (none if using Alpaca)

**Section 3: Conflicts**
"What are your legal obligations to me when acting as my investment adviser?"
- Fiduciary duty explanation
- How conflicts are managed
- More info available in ADV Part 2A

**Section 4: Compensation**
"How do your financial professionals make money?"
- Performance fees = incentive for risk
- How this affects recommendations

**Section 5: Disciplinary History**
"Do you or your financial professionals have legal or disciplinary history?"
- Yes/No
- Link to Investor.gov/CRS

**Section 6: Additional Information**
- How to get more info
- Contact information
- Complaint process

**Delivery Requirements:**
- At start of relationship (before account opening)
- Annually (if material changes)
- Must post on website (public access)
- Within 30 days of any material change

**Attorney Cost: $1,000-$2,000 to draft**

### Errors & Omissions (E&O) Insurance

**Required by Most States**

**Coverage Details:**
- **Minimum:** $1,000,000 per claim
- **Aggregate:** $1,000,000-$2,000,000
- **Covers:** Professional negligence, errors, omissions

**What It Covers:**
- Client alleges bad investment advice
- Algorithm error causes losses
- Breach of fiduciary duty claims
- Defense costs (attorney fees)
- Settlement/judgment amounts

**What It Doesn't Cover:**
- Intentional fraud
- Criminal acts
- Regulatory fines/penalties
- Punitive damages (varies by state)

**Annual Cost:**
- Small RIA (<$50M AUM): $2,000-$5,000/year
- Based on AUM, services offered, claims history

**Carriers:**
- Markel
- Houston Casualty Company
- CNA
- Philadelphia Insurance

### Ongoing Compliance Obligations

**Annual Requirements:**

| Task | Deadline | Details |
|------|----------|---------|
| Form ADV amendment | 90 days after fiscal year-end | Update any changes |
| Form CRS update | Within 30 days of changes | Material changes only |
| Annual compliance review | Within 12 months | Document findings in writing |
| State renewal fees | Varies by state | $100-$500 per state |
| E&O insurance renewal | Annual | Maintain continuous coverage |
| Books & records review | Ongoing | Ensure proper retention |

**Quarterly Requirements:**

| Task | Frequency | Details |
|------|-----------|---------|
| Personal securities reports | Quarterly | Your personal trades |
| Client statement review | Quarterly | Spot check accuracy |
| Performance calculations | Quarterly | Verify fee calculations |

**Ongoing Requirements:**

| Task | Timing | Details |
|------|--------|---------|
| Form ADV amendments | Within 30 days | Material changes |
| U4 updates | Within 30 days | Disciplinary events |
| Client communications | Retain 3+ years | All emails, messages |
| Trade confirmations | Retain 6 years | Every trade executed |
| Fee calculations | Retain 5 years | Spreadsheets, invoices |

**Annual Compliance Cost: $2,000-$5,000**
- Accounting/audit fees
- Form filings
- Review procedures
- Record maintenance

### Books & Records Requirements (Rule 204-2)

**Retention Periods:**

| Record Type | Retention | Format |
|-------------|-----------|--------|
| Client agreements | 6 years after termination | Original + electronic OK |
| Trade confirmations | 6 years | Electronic OK |
| Form ADV + amendments | 5 years | Electronic OK |
| Compliance policies | 5 years after superseded | Electronic OK |
| Client communications | 3 years | Email archives OK |
| Performance calculations | 5 years | Spreadsheets OK |
| Marketing materials | 5 years | Website archives OK |
| Financial statements | 6 years | Accounting software OK |

**Storage Requirements:**
- First 2 years: easily accessible
- Electronic storage: acceptable (cloud-based OK)
- Must be able to retrieve quickly for SEC exam
- Backup systems recommended

**Recommended Tools:**
- Google Workspace (email archives)
- Dropbox/Google Drive (documents)
- QuickBooks Online (financial records)
- Compliance software (Wealthbox, Redtail)

### State-by-State Multi-State Registration

**De Minimis Exemption:**
- Fewer than 6 clients in a state = no registration
- Exception: If you have office/place of business there

**Trigger for Additional State Registration:**

**Example:**
```
Your home state: Wyoming (registered)
Clients in California: 10
→ Must register in California

Clients in Texas: 4
→ No registration needed (under 6 clients)
```

**Process:**
1. Identify states with 6+ clients
2. File Form ADV with each state regulator
3. Pay state filing fees ($200-$500 each)
4. Comply with state-specific rules
5. Annual renewal in each state

**Cost per Additional State:**
- Initial filing: $200-$500
- Annual renewal: $100-$500
- Total over 5 years: $700-$3,000 per state

**State Contacts:**
- Find via NASAA (North American Securities Administrators Association)
- Each state has securities division website
- Most use IARD system (same as SEC)

### Stage 2 Total Compliance Costs

**Setup Costs (One-Time):**

| Item | Cost Range |
|------|------------|
| Securities attorney (Form ADV) | $5,000-$10,000 |
| Form ADV filing fees | $200-$500 |
| Series 65 exam + study | $400-$600 |
| Compliance manual drafting | $3,000-$8,000 |
| Form CRS drafting | $1,000-$2,000 |
| E&O insurance (first year) | $2,000-$5,000 |
| Custody procedures setup | $1,000-$2,000 |
| Recordkeeping system | $500-$1,000 |
| **TOTAL SETUP** | **$13,100-$29,100** |

**Annual Costs (Ongoing):**

| Item | Cost Range |
|------|------------|
| State registration renewals | $100-$500/year |
| E&O insurance | $2,000-$5,000/year |
| Annual compliance review | $1,000-$2,000/year |
| Accounting/tax filing | $2,000-$4,000/year |
| Recordkeeping/software | $500-$1,500/year |
| Form ADV amendments | $500-$1,000/year |
| Continuing education | $300-$500/year |
| **TOTAL ANNUAL** | **$6,400-$14,500/year** |

**Realistic Mid-Range:**
- Setup: $20,000
- Annual: $10,000

---

## PERFORMANCE FEE METHODOLOGY

### Overview

Performance fees are the most lucrative revenue model but also the most regulated aspect of investment advisory services.

**Key Principle:** Only charge fees on NEW profits above previous peak (high-water mark)

### Qualified Client Verification Process

**Step 1: Initial Screening**

Ask prospective client:
1. "Do you have investable assets of $1.1M+ to deposit?"
2. "OR, is your net worth $2.2M+ (excluding home)?"

If NO to both → Cannot charge performance fees

**Step 2: Documentation Request**

For AUM Test ($1.1M+ with you):
- Initial deposit confirmation
- Brokerage transfer statement
- Wire/ACH confirmation

For Net Worth Test ($2.2M+ net worth):
- Most recent tax return (Form 1040)
- CPA verification letter
- Account statements (all accounts, last 90 days)
- Real estate appraisals (if including property)
- Business valuations (if business owner)

**Step 3: Signed Representation**

Client must sign form stating they qualify

**Step 4: Annual Reverification**

- Review annually (circumstances change)
- If client falls below threshold:
  - Stop charging performance fees immediately
  - Switch to flat fee structure
  - Document the change

**Failure to verify = SEC violation**

### Performance Fee Structure Options

**Option A: Simple Profit Share (NOT RECOMMENDED)**

```
Month 1: $100K → $110K = $10K gain
Fee: $10K × 20% = $2,000

Month 2: $108K → $105K = -$3K loss
Fee: $0

Month 3: $105K → $115K = $10K gain
Fee: $10K × 20% = $2,000
```

**Problem:** Client back at same level as Month 1 ($115K vs $110K after fees), but you charged $4,000 total. Client paid twice for same gains.

**Option B: High-Water Mark (INDUSTRY STANDARD)**

```
Month 1: $100K → $110K
HWM: $100K (starting)
Profit above HWM: $10K
Fee: $10K × 20% = $2,000
New HWM: $110K

Month 2: $108K → $105K
HWM: $110K (previous)
Account below HWM: $105K
Fee: $0 (must recover first)
HWM unchanged: $110K

Month 3: $105K → $115K
HWM: $110K (previous)
Profit above HWM: $115K - $110K = $5K
Fee: $5K × 20% = $1,000
New HWM: $115K
```

**Benefit:** Client never pays twice for same gains

**RECOMMENDATION: Use High-Water Mark**

### High-Water Mark Calculation (Detailed)

**Variables to Track:**

1. **Beginning Balance** - Start of period (after previous fee)
2. **Deposits** - Money added by client mid-period
3. **Withdrawals** - Money removed by client mid-period
4. **Ending Balance** - End of period (before fee deduction)
5. **High-Water Mark** - Lifetime peak balance

**Basic Formula:**

```python
if ending_balance > high_water_mark:
    profit = ending_balance - high_water_mark
    fee = profit * 0.20
    new_balance = ending_balance - fee
    new_hwm = ending_balance
else:
    fee = 0
    new_balance = ending_balance
    new_hwm = high_water_mark  # unchanged
```

**Example Calculation (3 Months):**

**MONTH 1 (Initial Deposit):**
```
Starting: $0
Client deposits: $100,000
Ending balance (before fee): $105,000
Current HWM: $100,000 (initial deposit)

Calculation:
$105,000 > $100,000? YES
Profit above HWM: $105,000 - $100,000 = $5,000
Fee (20%): $5,000 × 0.20 = $1,000

After fee: $105,000 - $1,000 = $104,000
New HWM: $105,000
```

**MONTH 2 (Loss):**
```
Starting: $104,000
Ending balance (before fee): $102,000
Current HWM: $105,000

Calculation:
$102,000 > $105,000? NO
Below high-water mark

Fee: $0
After fee: $102,000
HWM unchanged: $105,000
```

**MONTH 3 (Recovery + New High):**
```
Starting: $102,000
Ending balance (before fee): $108,000
Current HWM: $105,000

Calculation:
$108,000 > $105,000? YES
Profit above HWM: $108,000 - $105,000 = $3,000
Fee (20%): $3,000 × 0.20 = $600

After fee: $108,000 - $600 = $107,400
New HWM: $108,000
```

### Handling Deposits & Withdrawals

**Problem:** Client deposits money mid-period. How to calculate?

**Solution: Proportional HWM Adjustment**

**Example:**

```
Day 1 of month:
Balance: $100,000
HWM: $100,000

Day 15 (mid-month):
Account value from trading: $105,000
Client deposits: $50,000
New balance: $155,000

Adjust HWM proportionally:
New HWM = Old HWM × (New Balance / Old Balance Before Deposit)
New HWM = $100,000 × ($155,000 / $105,000)
New HWM = $147,619

Rationale: The $50,000 deposit didn't come from your
trading, so shouldn't count toward HWM recovery.

End of month:
Balance: $160,000
Adjusted HWM: $147,619

Profit above HWM: $160,000 - $147,619 = $12,381
Fee: $12,381 × 20% = $2,476
```

**Alternative (Simpler):**
- Lock HWM calculation for month when deposit occurs
- Resume normal HWM tracking next month
- Avoids complex mid-period math

**For Withdrawals:**
- Reduce HWM proportionally
- Example: Client withdraws $50K from $150K account
  - New HWM = Old HWM × ($100K / $150K)
  - Prevents client from "resetting" HWM via withdrawal

### Fee Collection Frequency

**Monthly (Recommended for Retail):**

Pros:
- ✅ Steady cash flow for you
- ✅ Smaller fee amounts (less shock to client)
- ✅ More responsive to performance

Cons:
- ⚠️ More calculations (12× per year)
- ⚠️ More admin work

**Quarterly (Hedge Fund Standard):**

Pros:
- ✅ Less admin (4× per year)
- ✅ Industry standard
- ✅ Smooths out monthly volatility

Cons:
- ⚠️ Longer wait for revenue
- ⚠️ Larger fee amounts (may surprise clients)

**Annually:**

Pros:
- ✅ Simplest (once per year)

Cons:
- ❌ Very long wait for revenue
- ❌ Not typical for retail

**RECOMMENDATION: Monthly** (aligns with subscription model)

### Client Reporting Requirements

**Monthly Performance Statement (Required):**

```
═══════════════════════════════════════════════════════════
MONI MARKETS LLC - Monthly Performance Statement

Client: John Smith
Account: MS-12345
Period: January 2026
───────────────────────────────────────────────────────────

ACCOUNT SUMMARY:
Beginning Balance (Jan 1):              $100,000.00
Net Deposits/Withdrawals:                     $0.00
Gross Gain (before fees):                $10,500.00
Performance Fee (20%):                   -$2,100.00
Ending Balance (Jan 31):                $108,400.00

───────────────────────────────────────────────────────────

PERFORMANCE METRICS:
Monthly Return (before fees):               10.50%
Monthly Return (after fees):                 8.40%
YTD Return (after fees):                     8.40%
S&P 500 (Jan 2026):                          2.30%
Outperformance:                             +6.10%

───────────────────────────────────────────────────────────

FEE CALCULATION:
Account Value Before Fee:               $110,500.00
Previous High-Water Mark:               $100,000.00
New Profit Above HWM:                    $10,500.00
Performance Fee Rate:                          20%
Fee Charged:                             $2,100.00

Updated High-Water Mark:                $110,500.00

Note: Future fees only charged if account exceeds $110,500

───────────────────────────────────────────────────────────

TRADING ACTIVITY:
Total Trades: 12
Winning Trades: 8 (66.7%)
Losing Trades: 4 (33.3%)
Win Rate: 66.7%

───────────────────────────────────────────────────────────

NEXT STEPS:
- This fee will be deducted from your account within 5 days
- Your new high-water mark is $110,500
- Questions? support@monimarkets.com

═══════════════════════════════════════════════════════════
```

**Send within 5 business days of month-end**

### Fee Collection Methods

**Method 1: Direct Debit from Account (RECOMMENDED)**

Process:
1. Calculate fee at month-end
2. Execute sell orders to raise cash (if needed)
3. Transfer fee from client account to your business account
4. Alpaca API supports programmatic withdrawals

Client agreement must authorize:
```
"I authorize Moni Markets LLC to deduct performance fees
directly from my brokerage account at the end of each
period as calculated per the fee schedule."
```

Pros:
- ✅ Automatic (no chasing clients)
- ✅ Standard industry practice
- ✅ Client expects it

Cons:
- ⚠️ Requires liquidity (can't deduct if fully invested)
- ⚠️ Creates taxable event (selling securities)

**Method 2: Invoice Client (Not Recommended)**

Process:
1. Calculate fee
2. Email invoice to client
3. Client pays via ACH/wire

Pros:
- ✅ No need to liquidate positions

Cons:
- ❌ Payment delays/defaults
- ❌ Administrative burden
- ❌ Cash flow issues

### Recordkeeping Requirements

**Must Maintain for 6 Years:**

1. **Client Agreements**
   - Signed investment management agreement
   - Qualified client representation forms
   - Fee disclosure documents

2. **Performance Calculations**
   - Monthly fee calculation spreadsheets
   - High-water mark tracking per client
   - Adjustment calculations (deposits/withdrawals)

3. **Fee Invoices/Confirmations**
   - Fee deduction records
   - Bank transfer confirmations
   - Client statements showing fees

4. **Trade Records**
   - All trade confirmations (from Alpaca)
   - Order placement records
   - Execution reports

5. **Client Communications**
   - All emails regarding fees
   - Performance reports sent
   - Complaint correspondence

**Storage:**
- Electronic storage acceptable
- Cloud-based OK (Google Drive, Dropbox)
- Must be retrievable within 24 hours (SEC exam)
- Backup systems recommended

### Common Compliance Mistakes

**MISTAKE 1: Charging Below High-Water Mark**

Wrong:
```
Month 1: Account $100K → $110K, charge $2K
Month 2: Account drops to $105K, still charge $500
❌ Account is below $110K HWM!
```

Right:
```
Month 2: Account is $105K (below $110K HWM)
Fee = $0 until account recovers above $110K
```

**MISTAKE 2: No Qualified Client Verification**

Wrong:
```
Client: "I'm wealthy, trust me"
You: "Great! Sign here"
❌ No documentation
```

Right:
```
1. Request tax returns OR account statements
2. Get signed representation form
3. Document verification in client file
4. Keep records 6+ years
```

**MISTAKE 3: Changing Methodology Mid-Stream**

Wrong:
```
Months 1-6: Charge 20% on all gains
Month 7: Switch to high-water mark
❌ Client didn't agree to change
```

Right:
```
1. Fee methodology in written agreement
2. Any change requires client consent
3. Written amendment to agreement
4. File Form ADV amendment with SEC/state
```

**MISTAKE 4: Inadequate Disclosure**

Wrong:
```
Form ADV Part 2A doesn't mention performance fees
❌ SEC violation
```

Right:
```
Form ADV must include:
- Exact fee structure (20% of profits)
- High-water mark explanation
- Conflict of interest (incentive for risk)
- Calculation methodology
- Qualified client requirement
```

### Tax Implications

**For You (Business):**
- Performance fees = ordinary income
- Report on Form 1120 (C-Corp) or Schedule C (sole prop)
- Pay quarterly estimated taxes
- No special capital gains treatment

**For Client:**
- Performance fees are NOT tax-deductible (since 2018 tax reform)
- Client pays capital gains tax on investment gains
- Fee doesn't reduce taxable gain
- Client must understand this upfront

**Example Tax Impact:**
```
Client earns $10,000 in account
You charge $2,000 fee

Client's tax situation:
- Taxable gain: $10,000 (not $8,000)
- Fee paid: $2,000 (NOT deductible)
- Capital gains tax: ~15-20% on $10,000 = $1,500-$2,000
- Total cost: $2,000 fee + $1,500-$2,000 tax = $3,500-$4,000

Client net: $10,000 - $2,000 - $1,750 = $6,250
```

**Must disclose this in client agreement**

### Sample Performance Fee Clause

**For Investment Management Agreement:**

```
5. PERFORMANCE-BASED COMPENSATION

5.1 Fee Structure
Client agrees to pay Adviser a performance fee equal to
twenty percent (20%) of the net new profit in the Account,
calculated monthly.

5.2 High-Water Mark Method
Performance fees shall be calculated using the "high-water
mark" method. Fees are only charged on profits that exceed
the highest previous account value. If the Account value
declines below the high-water mark, no fee is charged until
the Account recovers and exceeds the previous peak.

5.3 Calculation Frequency
Fees calculated monthly as of the last business day of each
month.

5.4 Payment Method
Fees deducted directly from the Account within five (5)
business days of month-end. Client authorizes Adviser to
execute necessary transactions to raise cash for fee payment.

5.5 Qualified Client Status
Client represents meeting "Qualified Client" definition
under Rule 205-3:
  (a) $1,100,000+ under Adviser management, OR
  (b) $2,200,000+ net worth (excluding primary residence)

Client must notify Adviser immediately if no longer qualified.

5.6 Conflicts of Interest
Client acknowledges performance fees create incentive for
Adviser to take greater investment risk. Adviser mitigates
through diversified strategies and risk management protocols.

5.7 Tax Treatment
Client understands performance fees are NOT tax-deductible
under current federal law (IRC §67(g)).

5.8 Reporting
Adviser provides monthly performance statement showing fee
calculation, account performance, and high-water mark status.
```

---

## COMPARISON TABLES

### Stage 1 vs. Stage 2: Requirements

| Requirement | Signals-Only | Auto-Execution |
|-------------|--------------|----------------|
| **SEC/State Registration** | ❌ Not required | ✅ Required |
| **Form ADV Filing** | ❌ No | ✅ Yes |
| **Series 65 License** | ❌ No | ✅ Yes (or hire licensed person) |
| **Written Compliance Manual** | ❌ No | ✅ Yes |
| **Form CRS** | ❌ No | ✅ Yes |
| **Chief Compliance Officer** | ❌ No | ✅ Yes (can be you) |
| **E&O Insurance** | ⚠️ Optional | ✅ Required |
| **Custody Procedures** | ❌ No | ✅ Yes |
| **Performance Fee Restrictions** | N/A | ✅ Qualified clients only |
| **Annual Compliance Review** | ❌ No | ✅ Yes |
| **Books & Records (6 years)** | ⚠️ Basic | ✅ Extensive |
| **Client Agreements** | ⚠️ Terms of Service | ✅ Investment Management Agreement |
| **Regulatory Exam Risk** | Low | Moderate-High |

### Cost Comparison

| Cost Category | Signals-Only | Auto-Execution |
|---------------|--------------|----------------|
| **Setup (One-Time)** |
| Legal fees | $3,000-$5,000 | $8,000-$15,000 |
| Registration fees | $0 | $200-$1,500 |
| Exam fees | $0 | $400-$600 |
| Insurance (first year) | $0 | $2,000-$5,000 |
| Compliance systems | $500 | $2,000-$5,000 |
| **Total Setup** | **$3,500-$5,500** | **$12,600-$27,100** |
| | |
| **Annual (Ongoing)** |
| Registration renewals | $0 | $100-$500 |
| Insurance | $0 | $2,000-$5,000 |
| Compliance review | $0 | $1,000-$2,000 |
| Accounting/audit | $1,500-$2,500 | $2,500-$4,000 |
| Software/systems | $300-$500 | $1,000-$2,000 |
| Continuing education | $0 | $300-$500 |
| **Total Annual** | **$1,800-$3,000** | **$6,900-$14,000** |

### Timeline Comparison

| Milestone | Signals-Only | Auto-Execution |
|-----------|--------------|----------------|
| Business formation | 1 week | 1 week |
| Legal document drafting | 2-3 weeks | 4-6 weeks |
| Registration filing | N/A | Week 6 |
| State review period | N/A | 30-90 days |
| License exam | N/A | 2-3 months study |
| Insurance application | N/A | 1-2 weeks |
| **Total to Launch** | **3-4 weeks** | **3-6 months** |

### Revenue Models Comparison

| Model | Signals-Only | Auto-Execution (Flat) | Auto-Execution (Performance) |
|-------|--------------|----------------------|------------------------------|
| **Fee Structure** | $49-$199/month | $199-$499/month | 20% of profits |
| **Client Qualification** | None | None | Qualified clients only |
| **Predictable Revenue** | ✅ High | ✅ High | ❌ Variable |
| **Upside Potential** | ⚠️ Limited | ⚠️ Limited | ✅ Unlimited |
| **Client Alignment** | ⚠️ Medium | ⚠️ Medium | ✅ Very high |
| **Compliance Burden** | ✅ Low | ⚠️ High | ⚠️ Very high |
| **Cash Flow Stability** | ✅ Excellent | ✅ Excellent | ⚠️ Volatile |

---

## IMPLEMENTATION TIMELINE

### Phased Approach (Recommended)

**PHASE 1: MONTHS 0-6 (Signals-Only)**

**Month 1:**
- Week 1: Form Wyoming LLC, get EIN
- Week 2: Hire attorney for Terms of Service
- Week 3: Set up payment processing (Stripe)
- Week 4: Launch website + Discord

**Milestones:**
- ✅ First 10 paying subscribers
- ✅ Terms of Service live
- ✅ All disclaimers in place

**Month 2-3:**
- Grow subscriber base (target: 50-100)
- Refine signal delivery
- Track performance metrics
- Build community

**Milestones:**
- ✅ 50+ subscribers
- ✅ $5,000+ MRR
- ✅ Proven bot performance (3+ months data)

**Month 4-6:**
- Scale to 100-150 subscribers
- Prepare for Phase 2 (if desired)
- Save revenue for IA registration costs

**Milestones:**
- ✅ 100+ subscribers
- ✅ $10,000+ MRR
- ✅ $20,000 saved for IA registration

**Phase 1 Investment:**
- Setup: $5,000
- Operating: $2,000/year
- **Total Year 1: $7,000**

---

**PHASE 2: MONTHS 6-12 (Auto-Execution Prep)**

**Month 6:**
- Hire securities attorney
- Begin Form ADV drafting
- Start Series 65 study

**Month 7:**
- Continue Series 65 study
- Draft compliance manual
- Research E&O insurance

**Month 8:**
- File Form ADV with state
- Take Series 65 exam
- Apply for E&O insurance

**Month 9:**
- Respond to state deficiency letters
- Finalize compliance policies
- Begin Alpaca API integration

**Month 10:**
- Receive IA registration approval
- Complete Alpaca integration testing
- Update Terms of Service for auto-execution

**Month 11:**
- Beta test auto-execution (5-10 clients)
- Refine fee calculation systems
- Create client reporting templates

**Month 12:**
- Full launch of auto-execution tier
- Begin charging performance fees
- Onboard first 20-30 auto-execution clients

**Phase 2 Investment:**
- Setup: $20,000
- Operating: $10,000/year
- **Total: $30,000**

---

**PHASE 3: YEAR 2+ (Scale)**

**Goals:**
- 200+ total clients
- $50,000+ MRR
- 50+ auto-execution clients
- Multi-state registration (as needed)

---

## COMPLIANCE CHECKLISTS

### Stage 1: Signals-Only Launch Checklist

**Legal Formation:**
- [ ] Wyoming LLC formed
- [ ] EIN obtained from IRS
- [ ] Business bank account opened (Mercury)
- [ ] Stripe payment processing configured

**Legal Documentation:**
- [ ] Terms of Service drafted (attorney reviewed)
- [ ] Privacy Policy created
- [ ] Refund/Cancellation Policy created
- [ ] All disclaimers finalized

**Website/Platform:**
- [ ] Disclaimers on homepage (prominent display)
- [ ] Terms of Service linked (footer + signup)
- [ ] Privacy Policy accessible
- [ ] "Not investment advice" on every page
- [ ] "Not registered IA" disclosure

**Discord:**
- [ ] Disclaimers pinned in every channel
- [ ] Welcome message includes disclaimers
- [ ] Bot signals include risk warnings
- [ ] No personalized advice in DMs

**Operations:**
- [ ] Performance tracking system in place
- [ ] Signal delivery workflow tested
- [ ] Customer support email set up
- [ ] Billing system automated (Stripe)

**Compliance:**
- [ ] Basic recordkeeping system (Google Drive)
- [ ] Email archives enabled (3+ years)
- [ ] Copy of Terms saved (6+ years)
- [ ] Client communications logged

**Final Check:**
- [ ] All language is "information" not "advice"
- [ ] No personalized recommendations
- [ ] No portfolio reviews offered
- [ ] No discretionary authority
- [ ] Clear disclaimers everywhere

---

### Stage 2: Auto-Execution Launch Checklist

**Pre-Filing:**
- [ ] Securities attorney hired
- [ ] Form ADV Parts 1, 2A, 2B drafted
- [ ] Form CRS drafted
- [ ] Compliance manual completed
- [ ] Code of Ethics policy
- [ ] Best Execution policy
- [ ] Privacy/Cybersecurity policy
- [ ] Business Continuity Plan

**Registration:**
- [ ] Form ADV filed with state
- [ ] Filing fees paid ($200-$500)
- [ ] U4 form filed (background check)
- [ ] Series 65 exam passed (or licensed IAR hired)
- [ ] State approval received (letter on file)

**Insurance & Agreements:**
- [ ] E&O insurance obtained ($1M+ coverage)
- [ ] Policy documents on file
- [ ] Custodian agreement with Alpaca
- [ ] Investment Management Agreement template
- [ ] Qualified Client representation form

**Systems & Procedures:**
- [ ] High-water mark tracking spreadsheet
- [ ] Monthly performance reporting template
- [ ] Fee calculation automation
- [ ] Client communication system
- [ ] Recordkeeping system (6-year retention)

**Client Onboarding:**
- [ ] Qualified Client verification process
- [ ] Document collection checklist
- [ ] Signed agreements (original + copy)
- [ ] Form CRS delivered before account opening
- [ ] Form ADV Part 2A delivered
- [ ] Discretionary authority documented

**Ongoing Compliance:**
- [ ] Annual compliance review scheduled
- [ ] Quarterly personal trade reporting
- [ ] Monthly client statement generation
- [ ] Form ADV amendment process documented
- [ ] Client complaint procedure established

**Technology:**
- [ ] Alpaca API integration tested
- [ ] Order execution workflow verified
- [ ] Fee deduction automation
- [ ] Performance reporting automated
- [ ] Backup systems in place

**Final Verification:**
- [ ] All policies approved by CCO (you)
- [ ] All staff completed compliance training
- [ ] All recordkeeping systems tested
- [ ] Disaster recovery plan documented
- [ ] Ready for potential SEC exam

---

## APPENDICES

### Appendix A: Regulatory Contacts

**Federal:**
- SEC Division of Investment Management
- Website: www.sec.gov/investment
- Phone: (202) 551-6720

**IARD (Filing System):**
- Investment Adviser Registration Depository
- Website: www.iard.com
- Support: (240) 386-4848

**State Regulators:**
- NASAA (North American Securities Administrators Association)
- Website: www.nasaa.org
- Find your state regulator: www.nasaa.org/about-us/contact-us/

**Exam Registration:**
- FINRA (Series 65 exams)
- Website: www.finra.org
- Pearson VUE Test Centers: home.pearsonvue.com/finra

### Appendix B: Recommended Service Providers

**Securities Attorneys (IA Registration):**
- Morgan Lewis (Large firm)
- Practus LLP (Boutique RIA focus)
- Stark & Stark (Mid-Atlantic)
- Cost: $5,000-$15,000 for Form ADV

**E&O Insurance:**
- Markel Insurance
- Houston Casualty Company (HCC)
- CNA Financial
- Philadelphia Insurance
- Cost: $2,000-$5,000/year for small RIA

**Compliance Software:**
- Wealthbox CRM ($35-$50/month)
- Redtail CRM ($99/month)
- Orion Advisor ($1,000+/month - institutional)
- SmartRIA (compliance-focused, $200-$500/month)

**Recordkeeping:**
- Google Workspace (email archiving)
- Smarsh (compliance archiving, $20-$50/user/month)
- Global Relay (enterprise, $25-$40/user/month)

### Appendix C: Study Resources

**Series 65 Exam Prep:**
- Kaplan Financial Education
  - STC (Securities Training Corporation)
  - PassPerfect
  - Cost: $150-$300
  - Study time: 60-100 hours

**Free Resources:**
- SEC.gov (regulations and interpretations)
- NASAA.org (state regulations)
- FINRA.org (exam content outline)

**Books:**
- "Investment Adviser Regulation in a Nutshell" by Clifford Kirsch
- "Regulation of Investment Advisers" by Tamar Frankel

### Appendix D: Forms & Templates

**Available from your attorney:**
1. Form ADV Parts 1, 2A, 2B (SEC/state filing)
2. Form CRS (client relationship summary)
3. Investment Management Agreement
4. Qualified Client Representation Form
5. Code of Ethics
6. Compliance Manual template
7. Privacy Policy (Reg S-P compliant)
8. Business Continuity Plan template

**DIY Resources:**
- SEC IARD system has ADV templates
- SEC.gov has Form CRS examples
- Your attorney should provide all templates as part of engagement

### Appendix E: Key Regulations Reference

**Investment Advisers Act of 1940:**
- Section 202(a)(11): Definition of investment adviser
- Section 202(a)(11)(D): Publisher exemption
- Section 203: Registration requirements
- Section 204: Books and records
- Section 205: Performance fees
- Rule 204-2: Recordkeeping requirements
- Rule 205-3: Performance fees (qualified clients)
- Rule 206(4)-7: Compliance policies

**Full text:** www.sec.gov/investment/laws-and-rules

### Appendix F: Glossary

**AUM** - Assets Under Management. Total client assets you manage.

**Custody** - Having access to or control over client funds/securities.

**Discretionary Authority** - Power to make investment decisions without asking client first.

**Form ADV** - Registration form for investment advisers (SEC/state).

**Form CRS** - Client Relationship Summary (plain-English disclosure).

**IARD** - Investment Adviser Registration Depository (filing system).

**IA** - Investment Adviser (registered firm).

**IAR** - Investment Adviser Representative (licensed individual).

**High-Water Mark** - Highest previous account value (for performance fees).

**Qualified Client** - Client meeting Rule 205-3 thresholds ($1.1M or $2.2M).

**RIA** - Registered Investment Adviser.

**Series 65** - NASAA exam for investment adviser representatives.

**U4** - Uniform Application for Securities Industry Registration.

---

## DOCUMENT VERSION HISTORY

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | February 2026 | Initial release |

---

## DISCLAIMER

This guide is for informational purposes only and does not constitute legal advice. Securities regulations are complex and subject to change. Always consult with a qualified securities attorney before:

- Launching any investment-related service
- Filing Form ADV
- Charging performance fees
- Making any representations to clients

The author and Moni Markets LLC are not responsible for any regulatory violations resulting from use of this guide.

For legal advice specific to your situation, consult a securities attorney licensed in your jurisdiction.

---

**END OF DOCUMENT**

*Prepared by: Moni Markets LLC*
*Date: February 2026*
*© 2026 All Rights Reserved*
