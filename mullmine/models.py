import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=150, unique=True)
    is_verified = models.BooleanField(default=False)
    blocked_users = models.ManyToManyField("self")
    reported_users = models.ManyToManyField(
        "self", related_name="reported_by", symmetrical=False
    )
    is_online = models.BooleanField(default=False)


class ReportedChat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reported_room = models.ForeignKey(
        "Room", on_delete=models.CASCADE, related_name="reports"
    )
    reporter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reporter_chats"
    )
    reported = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reported_chats"
    )

    def __str__(self):
        return f"{self.reported} reported by {self.reporter}"


class Room(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    members = models.ManyToManyField(User)
    created_at = models.DateTimeField(auto_now_add=True)
    question = models.CharField(max_length=255)


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creator = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    latest_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
