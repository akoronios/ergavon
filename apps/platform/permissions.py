from django.core.exceptions import PermissionDenied

FINANCE_ROLES = ("admin", "finance")


def require_role(user, *roles: str) -> None:
    """Raise PermissionDenied unless the user is a superuser or holds one of the roles."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("A signed-in user is required.")
    if user.is_superuser:
        return
    if not user.roles.filter(name__in=roles).exists():
        names = " or ".join(r.capitalize() for r in roles)
        raise PermissionDenied(f"This needs the {names} role.")
