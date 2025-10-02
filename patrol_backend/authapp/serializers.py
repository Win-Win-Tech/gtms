from rest_framework import serializers
from .models import User
from scheduler.serializers import LocationSerializer as SchedulerLocationSerializer

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    location = SchedulerLocationSerializer(read_only=True)  # use existing serializer
    locationId = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'role', 'is_active', 'created_on', 
                  'modified_on', 'name', 'phone_no', 'location', 'locationId', 'aadhar_no']

    def create(self, validated_data):
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        if location_id:
            from scheduler.models import Location
            user.location = Location.objects.get(id=location_id)
        user.save()
        return user

    def update(self, instance, validated_data):
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        if location_id:
            from scheduler.models import Location
            instance.location = Location.objects.get(id=location_id)
        instance.save()
        return instance


# from rest_framework import serializers
# from .models import User


# class UserSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True)  # Accept 'password' for input but never return

#     class Meta:
#         model = User
#         fields = '__all__'  # ✅ include every field from User model
#         extra_kwargs = {
#             'password': {'write_only': True}
#         }

#     def create(self, validated_data):
#         password = validated_data.pop('password', None)
#         user = User(**validated_data)
#         if password:
#             user.set_password(password)
#         user.save()
#         return user
