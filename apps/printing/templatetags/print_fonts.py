"""{% print_font_face font %} and {% print_font_family font %} for labels and documents."""

from django import template

from apps.printing.fonts import family, font_face

register = template.Library()


@register.simple_tag
def print_font_face(font):
    return font_face(font)


@register.simple_tag
def print_font_family(font):
    return family(font)
