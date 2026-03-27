from rest_framework import serializers
from .models import Notification, NotificationRecipient
from django.contrib.auth import get_user_model

User = get_user_model()


class NotificationRecipientSerializer(serializers.ModelSerializer):

    class Meta:
        model = NotificationRecipient
        fields = ('id', 'is_read', 'read_at')


class NotificationSerializer(serializers.ModelSerializer):

    recipients = NotificationRecipientSerializer(many=True, read_only=True)

    class Meta:
        model = Notification
        fields = "__all__"
        read_only_fields = ('tenant', 'created_by', 'created_at')