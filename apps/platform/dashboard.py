"""Figures for the admin home page (Unfold DASHBOARD_CALLBACK)."""

from datetime import timedelta

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import BillableItem, BillingDraft
from apps.common import ZERO
from apps.subscriptions.models import Domain, ScheduledRun, Subscription
from apps.support.models import Ticket


def _money(value):
    return f"€ {value or ZERO:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dashboard_callback(request, context):
    today = timezone.localdate()
    tenant_id = getattr(request.user, "tenant_id", None)

    def scoped(qs):
        return qs.filter(tenant_id=tenant_id) if tenant_id else qs

    due = (BillingDraft.Status.APPROVED, BillingDraft.Status.SENT, BillingDraft.Status.INVOICED)
    open_drafts = scoped(BillingDraft.objects.filter(status__in=due, outstanding__gt=0))
    overdue = open_drafts.filter(due_date__lt=today)
    to_approve = scoped(BillingDraft.objects.filter(status=BillingDraft.Status.DRAFT))
    renewals = scoped(Subscription.objects.active().filter(
        status__in=["active", "overdue_payment"], renewal_date__range=(today, today + timedelta(days=30))
    ))
    domains = scoped(Domain.objects.active().filter(
        status__in=["active", "grace"], expiry_date__range=(today, today + timedelta(days=30))
    ))
    open_tickets = scoped(Ticket.objects.active().exclude(status__in=["resolved", "closed"]))
    breached = open_tickets.filter(resolution_due__lt=timezone.now())
    last_run = scoped(ScheduledRun.objects.filter(kind="billing")).order_by("-started_at").first()

    drafts_url = reverse("admin:billing_billingdraft_changelist")
    subs_url = reverse("admin:subscriptions_subscription_changelist")
    tickets_url = reverse("admin:support_ticket_changelist")

    context.update(
        {
            "kpis": [
                {
                    "title": "Outstanding",
                    "metric": _money(open_drafts.aggregate(s=Sum("outstanding"))["s"]),
                    "footer": f"{open_drafts.count()} open drafts",
                    "href": f"{drafts_url}?outstanding__gt=0",
                },
                {
                    "title": "Overdue",
                    "metric": _money(overdue.aggregate(s=Sum("outstanding"))["s"]),
                    "footer": f"{overdue.count()} drafts past due",
                    "href": f"{drafts_url}?due_date__lt={today.isoformat()}&outstanding__gt=0",
                },
                {
                    "title": "Drafts to approve",
                    "metric": to_approve.count(),
                    "footer": _money(to_approve.aggregate(s=Sum("gross"))["s"]) + " gross",
                    "href": f"{drafts_url}?status__exact=draft",
                },
                {
                    "title": "Active subscriptions",
                    "metric": scoped(Subscription.objects.active().filter(status="active")).count(),
                    "footer": f"{scoped(Subscription.objects.filter(status='overdue_payment')).count()} overdue payment",
                    "href": f"{subs_url}?status__exact=active",
                },
                {
                    "title": "Open tickets",
                    "metric": open_tickets.count(),
                    "footer": f"{breached.count()} past resolution target",
                    "href": tickets_url,
                },
                {
                    "title": "Unbilled items",
                    "metric": scoped(BillableItem.objects.filter(status="uninvoiced")).count(),
                    "footer": "not yet on a draft",
                    "href": reverse("admin:billing_billableitem_changelist") + "?status__exact=uninvoiced",
                },
            ],
            "renewals": {
                "headers": ["Renews", "Subscription", "Account"],
                "rows": [
                    [s.renewal_date.strftime("%d/%m/%Y"), str(s), str(s.account)]
                    for s in renewals.select_related("account", "product").order_by("renewal_date")[:8]
                ]
                + [
                    [d.expiry_date.strftime("%d/%m/%Y"), f"Domain {d.domain_name}", str(d.account)]
                    for d in domains.select_related("account").order_by("expiry_date")[:8]
                ],
            },
            "overdue_drafts": {
                "headers": ["Draft", "Account", "Due", "Outstanding"],
                "rows": [
                    [d.number, str(d.account), d.due_date.strftime("%d/%m/%Y"), _money(d.outstanding)]
                    for d in overdue.select_related("account").order_by("due_date")[:8]
                ],
            },
            "last_run": last_run,
            "runs_url": reverse("admin:subscriptions_scheduledrun_changelist"),
        }
    )
    return context


def environment_callback(request):
    """Label shown in the admin header so nobody mistakes the beta for production."""
    from django.conf import settings

    return ["Beta", "warning"] if settings.DEBUG else ["Beta", "info"]
