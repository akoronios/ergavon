"""Audit log writer (NFR-03.1).

Every create, update, delete and status change on an ERGAVON model is written
to AuditLog with the user, the time and the old and new values of the fields
that changed. Services mark approvals and other named actions with
`audit_action(...)`.

Who did it comes from a context variable: the web middleware sets it per
request, and services, commands and tests set it with `acting_as(user)`.
"""

from __future__ import annotations

import contextlib
import contextvars
import json

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save

AUDITED_APPS = {"platform", "crm", "catalogue", "sales", "subscriptions", "projects", "billing", "support", "calendar"}
SKIP_MODELS = {"platform.AuditLog", "platform.Sequence", "platform.Tenant", "subscriptions.ScheduledRun"}
SKIP_FIELDS = {"created_at", "updated_at", "created_by", "updated_by", "search_name", "password", "last_login"}

_current_user = contextvars.ContextVar("ergavon_audit_user", default=None)
_current_action = contextvars.ContextVar("ergavon_audit_action", default=None)


@contextlib.contextmanager
def acting_as(user):
    token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(token)


@contextlib.contextmanager
def audit_action(action: str, note: str = ""):
    """Label the saves inside this block, e.g. an approval or a payment allocation."""
    token = _current_action.set((action, note))
    try:
        yield
    finally:
        _current_action.reset(token)


def current_user():
    return _current_user.get()


class AuditUserMiddleware:
    """Make request.user the acting user for everything saved during the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _current_user.set(user if user is not None and user.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            _current_user.reset(token)


# ---------------------------------------------------------------- helpers


def _label(model) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _audited(model) -> bool:
    return model._meta.app_label in AUDITED_APPS and _label(model) not in SKIP_MODELS


def _values(instance) -> dict:
    out = {}
    for f in instance._meta.concrete_fields:
        if f.name in SKIP_FIELDS or f.primary_key:
            continue
        out[f.attname] = getattr(instance, f.attname)
    # Normalise to JSON-safe values once, so comparisons are like for like
    return json.loads(json.dumps(out, cls=DjangoJSONEncoder))


def _tenant_id(instance):
    if getattr(instance, "tenant_id", None):
        return instance.tenant_id
    for f in instance._meta.concrete_fields:
        if isinstance(f, models.ForeignKey) and any(g.name == "tenant" for g in f.related_model._meta.fields):
            parent_id = getattr(instance, f.attname)
            if parent_id:
                return f.related_model.objects.filter(pk=parent_id).values_list("tenant_id", flat=True).first()
    return None


def write(instance, action: str, old: dict | None, new: dict | None, note: str = ""):
    from django.contrib.contenttypes.models import ContentType

    from apps.platform.models import AuditLog

    tenant_id = _tenant_id(instance)
    if tenant_id is None:
        return None
    user = current_user()
    return AuditLog.objects.create(
        tenant_id=tenant_id,
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
        action=action,
        note=note[:255],
        user=user if getattr(user, "pk", None) else None,
        old_values=old or {},
        new_values=new or {},
    )


# ---------------------------------------------------------------- signals


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _audited(sender):
        return
    instance._audit_old = None
    if instance.pk and not instance._state.adding:
        previous = sender._base_manager.filter(pk=instance.pk).first()
        if previous is not None:
            instance._audit_old = _values(previous)


def _post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _audited(sender):
        return
    named = _current_action.get()
    new = _values(instance)
    if created:
        action, note = named or ("create", "")
        write(instance, action, None, new, note)
        return
    old = getattr(instance, "_audit_old", None) or {}
    changed = {k for k in new if old.get(k) != new[k]}
    if not changed:
        return
    if named:
        action, note = named
    elif "status" in changed:
        action, note = "status", f"{old.get('status')} → {new.get('status')}"
    elif "archived_at" in changed:
        action, note = "delete", "archived"
    else:
        action, note = "update", ""
    write(instance, action, {k: old.get(k) for k in changed}, {k: new[k] for k in changed}, note)


def _post_delete(sender, instance, **kwargs):
    if not _audited(sender):
        return
    write(instance, "delete", _values(instance), None, "deleted")


def connect():
    for model in apps.get_models():
        if _audited(model):
            uid = _label(model)
            pre_save.connect(_pre_save, sender=model, dispatch_uid=f"audit-pre-{uid}")
            post_save.connect(_post_save, sender=model, dispatch_uid=f"audit-post-{uid}")
            post_delete.connect(_post_delete, sender=model, dispatch_uid=f"audit-del-{uid}")
