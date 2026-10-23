# ERGAVON

Billing, subscription and operations platform (merged ERGAVON / SMA-A product).
Backend: Django 5.2 + PostgreSQL. The React front end comes in a later step.

This first commit holds the v0.1 data model: 9 apps, 53 tables, the first
migrations, the Django admin for internal testing, a tenant seed command and
tests for the core business rules.

## Apps

| App | Tables |
| --- | --- |
| platform | Tenant, User, Role, UserRole, AuditLog, Attachment, Sequence, CustomField, CustomFieldValue |
| crm | Account, AccountRole, Contact, Membership, Lead, Opportunity, Activity |
| catalogue | TaxCategory, Product, Pricelist, PricelistItem, Asset |
| sales | Quote, QuoteVersion, QuoteLine, Order, OrderLine |
| subscriptions | Subscription, SubscriptionPrice, Domain, ScheduledRun |
| projects | Project, ProjectSubscription, ProjectTicket, ProjectLink, Task, TimeEntry, ProjectCost |
| billing | BillableItem, BillingDraft, DraftLine, Payment, PaymentAllocation, CreditEntry, AccountingExport, ExportedRecord |
| support | Department, SlaPolicy, Ticket, TicketMessage, TicketStatusChange, PredefinedReply |
| calendar | Event, Reminder |

Shared base classes live in `apps/common.py`: tenant, UUID keys, audit
columns, soft delete for business records, no deletes for financial records,
`Decimal(14,2)` money with a currency code.

## Rules the schema enforces

- One billable item per subscription period and per domain renewal period
- An invoiced billing draft must carry the external invoice number and date
- A time entry belongs to a task or a ticket, never both; only approved billable time is billed
- A quote belongs to an account or a lead
- A draft or payment is exported to accounting only once
- ΑΦΜ is validated by check digit for Greek accounts
- Account search ignores Greek accents and case; Greeklish helper included

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set DATABASE_URL and SECRET_KEY
createdb ergavon
python manage.py migrate
python manage.py seed_tenant "WEDEVA" wedeva
python manage.py createsuperuser
python manage.py runserver        # admin at http://localhost:8000/admin/
python manage.py test apps        # 63 tests, needs PostgreSQL
```

`seed_tenant` creates the five roles, Greek VAT categories (24, 13, 6, island
17/9/4, exempt, EU reverse charge), placeholder SLA targets per priority, a
Support department and numbering prefixes. Confirm the VAT and SLA values
before the first billing run.

## Billing run

`python manage.py run_billing [--tenant wedeva] [--until YYYY-MM-DD] [--terms 14]`

Bills in advance every subscription period and domain renewal that starts on
or before `--until` (default today), then puts each customer's new items on
one billing draft. Schedule it daily with cron. Every run is logged in
Scheduled runs (admin), where an Admin can retry a failed run.

- Idempotent: re-running or retrying never bills a period twice
- Catch-up: missed runs produce one item per missed period
- Anchor days 29 to 31 clamp to the month's last day, then return
- Price changes apply from the period they are valid from
- Suspended: periods are skipped; billing resumes from the next period, no back-billing
- Overdue Payment: billing holds and catches up once Active again, unless
  "keep active when overdue" is set, which keeps billing
- Cancelled: periods starting after the effective date are not billed
- Domains with auto-renew bill one item per renewal term
- A draft line carries the project only when the subscription belongs to exactly one project
- An error on one subscription or domain is recorded and never blocks the others

## Billing drafts and payments

Services in `apps/billing/lifecycle.py` and `apps/billing/payments.py`; the
admin offers approve, reopen, send, reissue and payment auto-allocation.

- Draft → Approved → Sent to accounting → Invoiced. Approved can be reopened to Draft.
- Invoiced needs the external invoice number and date, marks the items Billed and
  locks the draft; only payments can still be allocated.
- Void (Draft, Approved or Sent, with a reason, no payments allocated) returns the
  items to Uninvoiced; Reissue puts them on a new draft.
- Lines and totals are frozen once a draft leaves Draft.
- Payments are allocated to one or more drafts, partially if needed, never beyond
  the payment or the amount outstanding. Unallocated money is the customer's credit
  and can be applied to later drafts.
- A subscription turns Overdue Payment while one of its drafts is past due and
  unpaid, and back to Active when paid; this is checked at every billing run and
  every allocation.
- Approve, send, invoice, void, reissue and payments need the Finance or Admin role.

## Audit log

Every create, update, delete and status change on ERGAVON records is written to
the audit log with the user and the old and new values of the changed fields.
Approvals, voids and allocations are labelled as such.

## Admin (internal beta interface)

The Django admin runs on the Unfold theme until the React UI exists:
http://localhost:8000/admin/ (the site root redirects there).

- Dashboard: outstanding and overdue amounts, drafts to approve, active and
  overdue subscriptions, open tickets, unbilled items, overdue drafts,
  renewals and domain expiries in the next 30 days, last billing run
- Sidebar grouped as Billing, Customers, Services, Work and Settings
- Billing draft page buttons: Approve, Reopen, Sent to accounting,
  Mark invoiced (asks for the invoice number and date), Void (asks for a
  reason), Reissue; drafts and payments can never be deleted
- Payment page buttons: Allocate to a draft, Auto-allocate
- Scheduled runs: "Run billing now" (Admin only) and retry
- Account search ignores Greek accents and case
- Audit log is read-only

For production (`DEBUG=False`) run `python manage.py collectstatic` after each
deploy; WhiteNoise serves the files, so the web server needs no extra setup.

## Deploying on Plesk

Step-by-step guide: [docs/DEPLOY_PLESK.md](docs/DEPLOY_PLESK.md). Updates afterwards: `./deploy.sh`.

## Not built yet

Reminder run, API, PDFs and the React UI. Accounting export is out of scope for
now. Jira links and myDATA come after the beta.
