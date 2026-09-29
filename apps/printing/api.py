from rest_framework import serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from . import services
from .models import LabelTemplate


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelTemplate
        fields = ["id", "name", "media", "layout", "width_mm", "height_mm", "tail_mm", "fields",
                  "fields_b", "font_size_pt", "barcode_height_mm", "page_width_mm",
                  "page_height_mm", "columns", "rows", "margin_top_mm", "margin_start_mm",
                  "gap_x_mm", "gap_y_mm", "is_default", "is_active"]


class LabelTemplateViewSet(viewsets.GenericViewSet):
    """Validation lives in services.template_from; the body is passed through as a dict."""

    queryset = LabelTemplate.objects.all()
    serializer_class = TemplateSerializer
    required_permissions = {
        "list": "inventory.item.print_label",
        "retrieve": "inventory.item.print_label",
        "create": services.MANAGE,
        "update": services.MANAGE,
        "destroy": services.MANAGE,
    }

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        template = services.create_template(dict(request.data), actor=request.actor)
        return Response(self.get_serializer(template).data, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        template = services.update_template(int(pk), dict(request.data), actor=request.actor)
        return Response(self.get_serializer(template).data)

    def destroy(self, request, pk=None):
        services.delete_template(int(pk), actor=request.actor)
        return Response(status=status.HTTP_204_NO_CONTENT)


router = SimpleRouter()
router.register("templates", LabelTemplateViewSet, basename="label-template")

urlpatterns = router.urls
