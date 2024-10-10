import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from blabhere.models import Room, Message

logger = logging.getLogger(__name__)


def get_room(room_id):
    room, created = Room.objects.get_or_create(
        id=room_id,
        defaults={
            "display_name": "A new room name",
        },
    )
    return room


def get_all_member_display_names(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("display_name")
    return [member["display_name"] for member in members]


def add_user_to_room(user, room):
    was_added = False
    if user not in room.members.all():
        room.members.add(user)
        was_added = True
    member_display_names = get_all_member_display_names(room.id)
    return member_display_names, was_added


def get_initial_messages(room):
    messages = [
        {
            "creator_username": msg.creator.username,
            "creator_display_name": msg.creator.display_name,
            "content": msg.content,
            "created_at": msg.created_at.timestamp(),
            "id": str(msg.id),
        }
        for msg in room.message_set.order_by("-created_at")[:10][::-1]
    ]
    return messages


def create_new_message(content, room, creator):

    new_message = Message.objects.create(creator=creator, room=room, content=content)
    return {
        "creator_username": new_message.creator.username,
        "creator_display_name": new_message.creator.display_name,
        "content": new_message.content,
        "created_at": new_message.created_at.timestamp(),
        "id": str(new_message.id),
    }


def get_prev_messages(oldest_msg_id, room):
    messages = []
    oldest_msg = Message.objects.filter(id=oldest_msg_id, room=room)
    if oldest_msg.exists():
        oldest_msg = oldest_msg.first()
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(
                created_at__lt=oldest_msg.created_at
            ).order_by("-created_at")[:10][::-1]
        ]
    return messages


class RoomConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_id = None
        self.user = None

    async def connect(self):
        await self.accept()
        self.user = self.scope["user"]

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(str(self.room_id), self.channel_name)

    async def fetch_display_name(self, room):
        display_name = room.display_name
        await self.channel_layer.send(
            self.channel_name,
            {"type": "display_name", "display_name": display_name},
        )

    async def fetch_member_display_names(self, room):
        member_display_names, was_added = await database_sync_to_async(
            add_user_to_room
        )(self.user, room)
        if was_added:
            await self.channel_layer.group_send(
                self.room_id, {"type": "members", "members": member_display_names}
            )
        else:
            await self.channel_layer.send(
                self.channel_name, {"type": "members", "members": member_display_names}
            )

    async def fetch_initial_messages(self, room):
        messages = await database_sync_to_async(get_initial_messages)(room)
        await self.channel_layer.send(
            self.channel_name, {"type": "messages", "messages": messages}
        )

    async def fetch_prev_messages(self, input_payload):
        oldest_msg_id = input_payload.get("oldest_message_id", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        messages = await database_sync_to_async(get_prev_messages)(oldest_msg_id, room)
        await self.channel_layer.send(
            self.channel_name, {"type": "messages", "messages": messages}
        )

    async def initialize_room(self):
        await self.channel_layer.group_add(self.room_id, self.channel_name)
        room = await database_sync_to_async(get_room)(self.room_id)
        await self.fetch_member_display_names(room)
        await self.fetch_display_name(room)
        await self.fetch_initial_messages(room)

    async def send_message(self, input_payload):
        message = input_payload.get("message", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        creator = self.user
        if len(message.strip()) > 0:
            new_message = await database_sync_to_async(create_new_message)(
                message, room, creator
            )
            await self.channel_layer.group_send(
                self.room_id,
                {"type": "new_message", "new_message": new_message},
            )

    async def receive_json(self, content, **kwargs):
        if content.get("command") == "connect":
            self.room_id = content.get("room")
            asyncio.create_task(self.initialize_room())
        if content.get("command") == "send_message":
            asyncio.create_task(self.send_message(content))
        if content.get("command") == "fetch_prev_messages":
            asyncio.create_task(self.fetch_prev_messages(content))

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def members(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def new_message(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def messages(self, event):
        # Send message to WebSocket
        await self.send_json(event)
