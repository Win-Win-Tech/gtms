from rest_framework import serializers
from .models import User
from django.contrib.auth.hashers import make_password

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)  # Accepts 'password' from input

    class Meta:
        model = User
        #fields = ['id', 'email', 'password_hash', 'role', 'is_active', 'created_at', 'updated_at']
        fields = ['id', 'email', 'password', 'role', 'is_active', 'created_at', 'updated_at']
        #extra_kwargs = {'password_hash': {'write_only': True}}

    #def create(self, validated_data):
    #    validated_data['password_hash'] = make_password(validated_data['password_hash'])
    #    return super().create(validated_data)

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user