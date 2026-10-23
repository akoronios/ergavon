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
python manage.py test apps        # 16 rule tests, needs PostgreSQL
```

`seed_tenant` creates the five roles, Greek VAT categories (24, 13, 6, island
17/9/4, exempt, EU reverse charge), placeholder SLA targets per priority, a
Support department and numbering prefixes. Confirm the VAT and SLA values
before the first billing run.

## Not in this commit

Billing run, reminder run, audit-log writer, permissions per role, API,
CSV export mapping, PDFs and the React UI. Jira links and myDATA come after
the beta.
