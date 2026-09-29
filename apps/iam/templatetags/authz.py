from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def can(context, code, branch=None):
    """{% can "pricing.board.publish" as allowed %}: for showing/hiding UI only (§14.3).
    The server checks the permission again on every request."""
    actor = getattr(context.get("request"), "actor", None)
    return bool(actor is not None and actor.can(code, branch))
