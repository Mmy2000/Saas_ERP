from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.org import services
from apps.org.models import Branch

from .serializers import BranchSerializer


class BranchWriteSerializer(serializers.Serializer):
    code = serializers.IntegerField(min_value=1, max_value=999)
    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    handles_diamonds = serializers.BooleanField(required=False, default=False)
    is_head_office = serializers.BooleanField(required=False, default=False)


class BranchViewSet(viewsets.GenericViewSet):
    # Built at import time with no tenant context; bound to the request's tenant when DRF
    # clones it in get_queryset().
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    filterset_fields = ["is_active", "is_head_office"]
    required_permissions = {
        "list": "org.branch.view",
        "retrieve": "org.branch.view",
        "create": "org.branch.manage",
        "update": "org.branch.manage",
        "deactivate": "org.branch.manage",
        "activate": "org.branch.manage",
    }

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def _input(self):
        payload = BranchWriteSerializer(data=self.request.data)
        payload.is_valid(raise_exception=True)
        return services.BranchInput(**payload.validated_data)

    def create(self, request):
        branch = services.create_branch(self._input(), actor=request.actor)
        return Response(self.get_serializer(branch).data, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        branch = services.update_branch(int(pk), self._input(), actor=request.actor)
        return Response(self.get_serializer(branch).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        branch = services.set_branch_active(int(pk), False, actor=request.actor)
        return Response(self.get_serializer(branch).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        branch = services.set_branch_active(int(pk), True, actor=request.actor)
        return Response(self.get_serializer(branch).data)
