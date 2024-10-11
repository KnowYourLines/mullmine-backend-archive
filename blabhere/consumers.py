import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from blabhere.helpers import (
    add_user_to_room,
    get_room,
    get_prev_messages,
    get_initial_messages,
    create_new_message,
    update_room_name,
    change_user_display_name,
    get_all_member_display_names,
    get_user,
    initialize_room,
)

logger = logging.getLogger(__name__)


class UserConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.username = None
        self.user = None

    async def fetch_display_name(self):
        display_name = self.user.display_name
        await self.channel_layer.send(
            self.channel_name,
            {"type": "display_name", "display_name": display_name},
        )

    async def connect(self):
        self.username = str(self.scope["url_route"]["kwargs"]["user_id"])
        self.user = self.scope["user"]
        if self.username == self.user.username:
            await self.channel_layer.group_add(self.username, self.channel_name)
            await self.accept()
            await self.fetch_display_name()
        else:
            await self.close()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.username, self.channel_name)

    async def update_display_name(self, input_payload):
        new_display_name = input_payload.get("new_display_name", "")
        if len(new_display_name.strip()) > 0:
            (
                display_name,
                rooms_to_refresh,
            ) = await database_sync_to_async(
                change_user_display_name
            )(self.user, new_display_name)
            for room_id in rooms_to_refresh:
                members = await database_sync_to_async(get_all_member_display_names)(
                    room_id
                )
                await self.channel_layer.group_send(
                    room_id, {"type": "members", "members": members}
                )
                room = await database_sync_to_async(get_room)(room_id)
                messages = await database_sync_to_async(get_initial_messages)(room)
                await self.channel_layer.group_send(
                    room_id,
                    {
                        "type": "refreshed_messages",
                        "refreshed_messages": messages,
                    },
                )
            await self.channel_layer.group_send(
                self.username,
                {
                    "type": "display_name",
                    "display_name": display_name,
                },
            )

    async def receive_json(self, content, **kwargs):
        if self.username == self.user.username:
            if content.get("command") == "update_display_name":
                asyncio.create_task(self.update_display_name(content))

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)


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
        room = await database_sync_to_async(initialize_room)(self.room_id, self.user)
        await self.fetch_member_display_names(room)
        await self.fetch_display_name(room)
        await self.fetch_initial_messages(room)

    async def send_message(self, input_payload):
        message = input_payload.get("message", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        creator = await database_sync_to_async(get_user)(self.user.username)
        if len(message.strip()) > 0:
            new_message = await database_sync_to_async(create_new_message)(
                message, room, creator
            )
            await self.channel_layer.group_send(
                self.room_id,
                {"type": "new_message", "new_message": new_message},
            )

    async def update_display_name(self, input_payload):
        new_display_name = input_payload.get("new_display_name", "")
        if len(new_display_name.strip()) > 0:
            room = await database_sync_to_async(get_room)(self.room_id)
            await database_sync_to_async(update_room_name)(new_display_name, room)
            await self.channel_layer.group_send(
                self.room_id,
                {"type": "display_name", "display_name": self.user.display_name},
            )

    async def receive_json(self, content, **kwargs):
        if content.get("command") == "connect":
            self.room_id = content.get("room")
            asyncio.create_task(self.initialize_room())
        if content.get("command") == "send_message":
            asyncio.create_task(self.send_message(content))
        if content.get("command") == "fetch_prev_messages":
            asyncio.create_task(self.fetch_prev_messages(content))
        if content.get("command") == "update_display_name":
            asyncio.create_task(self.update_display_name(content))

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

    async def refreshed_messages(self, event):
        # Send message to WebSocket
        await self.send_json(event)
