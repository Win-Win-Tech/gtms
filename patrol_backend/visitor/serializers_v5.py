from .serializers import VisitorEntrySerializer


class VisitorEntrySerializerV5(VisitorEntrySerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["site_id"] = str(instance.site_id) if instance.site_id else None
        data["site_name"] = instance.site.name if instance.site else None
        return data
