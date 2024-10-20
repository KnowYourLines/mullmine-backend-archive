import asyncio
import datetime
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
    get_refreshed_messages,
    update_member_limit,
    check_room_full,
    is_room_creator,
    get_num_room_members,
    get_user_conversations,
    get_all_member_usernames,
    read_unread_conversation,
    leave_room,
    room_search,
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

    async def fetch_conversations(self):
        conversations = await database_sync_to_async(get_user_conversations)(
            self.user.username
        )
        await self.channel_layer.send(
            self.channel_name,
            {"type": "conversations", "conversations": conversations},
        )

    async def exit_room(self, input_payload):
        await database_sync_to_async(leave_room)(self.user, input_payload["room_id"])
        await self.channel_layer.group_send(
            self.user.username,
            {"type": "refresh_conversations"},
        )
        members = await database_sync_to_async(get_all_member_display_names)(
            input_payload["room_id"]
        )
        await self.channel_layer.group_send(
            input_payload["room_id"], {"type": "members", "members": members}
        )
        await self.channel_layer.group_send(
            input_payload["room_id"],
            {"type": "user_left_room", "user_left_room": self.user.username},
        )

    async def connect(self):
        self.username = str(self.scope["url_route"]["kwargs"]["user_id"])
        self.user = self.scope["user"]
        if self.username == self.user.username:
            await self.channel_layer.group_add(self.username, self.channel_name)
            await self.accept()
            await self.fetch_conversations()
            await self.fetch_display_name()
        else:
            await self.close()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.username, self.channel_name)

    async def update_display_name(self, input_payload):
        new_display_name = input_payload.get("new_display_name", "")
        if len(new_display_name.strip()) > 0:
            (succeeded, display_name, rooms_to_refresh, users_to_refresh) = (
                await database_sync_to_async(change_user_display_name)(
                    self.user, new_display_name
                )
            )
            if succeeded:
                for username in users_to_refresh:
                    await self.channel_layer.group_send(
                        username,
                        {"type": "refresh_conversations"},
                    )
                for room_id in rooms_to_refresh:
                    members = await database_sync_to_async(
                        get_all_member_display_names
                    )(room_id)
                    await self.channel_layer.group_send(
                        room_id, {"type": "members", "members": members}
                    )
                    await self.channel_layer.group_send(
                        room_id,
                        {
                            "type": "refreshed_messages",
                        },
                    )
                await self.channel_layer.group_send(
                    self.username,
                    {
                        "type": "display_name",
                        "display_name": display_name,
                    },
                )
            else:
                await self.channel_layer.send(
                    self.channel_name,
                    {
                        "type": "display_name_taken",
                        "display_name_taken": new_display_name,
                    },
                )

    async def receive_json(self, content, **kwargs):
        if self.username == self.user.username:
            if content.get("command") == "exit_room":
                asyncio.create_task(self.exit_room(content))
            if content.get("command") == "update_display_name":
                asyncio.create_task(self.update_display_name(content))

    async def display_name_taken(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def conversations(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def refresh_conversations(self, event):
        # Send message to WebSocket
        await self.fetch_conversations()


class RoomConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_id = None
        self.user = None
        self.oldest_message_timestamp = None
        self.room_search_page = 1

    async def connect(self):
        await self.accept()
        self.user = self.scope["user"]
        await self.initial_room_search()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(str(self.room_id), self.channel_name)

    async def initial_room_search(self):
        results = await database_sync_to_async(room_search)(
            self.room_search_page, self.user
        )
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "room_search_results",
                "room_search_results": results,
            },
        )

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
            await self.channel_layer.group_send(
                self.user.username,
                {"type": "refresh_conversations"},
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

    async def fetch_is_creator(self, room):
        is_creator = await database_sync_to_async(is_room_creator)(room, self.user)
        await self.channel_layer.send(
            self.channel_name,
            {"type": "is_room_creator", "is_room_creator": is_creator},
        )

    async def fetch_member_limit(self, room):
        await self.channel_layer.send(
            self.channel_name,
            {"type": "member_limit", "member_limit": room.max_num_members},
        )

    async def fetch_prev_messages(self, input_payload):
        oldest_msg_id = input_payload.get("oldest_message_id", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        messages = await database_sync_to_async(get_prev_messages)(oldest_msg_id, room)
        await self.channel_layer.send(
            self.channel_name, {"type": "messages", "messages": messages}
        )

    async def read_conversation(self):
        await database_sync_to_async(read_unread_conversation)(self.room_id, self.user)
        await self.channel_layer.group_send(
            self.user.username,
            {
                "type": "refresh_conversations",
            },
        )

    async def initialize_room(self):
        is_room_full = await database_sync_to_async(check_room_full)(
            self.room_id, self.user
        )
        if not is_room_full:
            await self.channel_layer.group_add(self.room_id, self.channel_name)
            room = await database_sync_to_async(initialize_room)(
                self.room_id, self.user
            )
            await self.fetch_member_display_names(room)
            await self.fetch_display_name(room)
            await self.fetch_initial_messages(room)
            await self.fetch_member_limit(room)
            await self.fetch_is_creator(room)
            await self.read_conversation()
        else:
            await self.channel_layer.send(
                self.channel_name, {"type": "is_room_full", "is_room_full": True}
            )

    async def send_message(self, input_payload):
        message = input_payload.get("message", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        creator = await database_sync_to_async(get_user)(self.user.username)
        if len(message.strip()) > 0:
            new_message = await database_sync_to_async(create_new_message)(
                message, room, creator
            )
            usernames = await database_sync_to_async(get_all_member_usernames)(
                self.room_id
            )
            for username in usernames:
                await self.channel_layer.group_send(
                    username,
                    {"type": "refresh_conversations"},
                )
            await self.channel_layer.group_send(
                self.room_id,
                {"type": "new_message", "new_message": new_message},
            )

    async def update_display_name(self, input_payload):
        new_display_name = input_payload.get("new_display_name", "")
        if len(new_display_name.strip()) > 0:
            room = await database_sync_to_async(get_room)(self.room_id)
            succeeded = await database_sync_to_async(update_room_name)(
                new_display_name, room
            )
            if succeeded:
                await self.channel_layer.group_send(
                    self.room_id,
                    {"type": "display_name", "display_name": room.display_name},
                )
                usernames = await database_sync_to_async(get_all_member_usernames)(
                    self.room_id
                )
                for username in usernames:
                    await self.channel_layer.group_send(
                        username,
                        {"type": "refresh_conversations"},
                    )
            else:
                await self.channel_layer.send(
                    self.channel_name,
                    {
                        "type": "display_name_taken",
                        "display_name_taken": new_display_name,
                    },
                )

    async def update_member_limit(self, input_payload):
        room = await database_sync_to_async(get_room)(self.room_id)
        num_members = await database_sync_to_async(get_num_room_members)(room)
        new_limit = int(input_payload.get("max_num_members", None))
        if new_limit and new_limit >= num_members:
            room = await database_sync_to_async(update_member_limit)(
                new_limit, room, self.user
            )
            await self.channel_layer.group_send(
                self.room_id,
                {"type": "member_limit", "member_limit": room.max_num_members},
            )

    async def receive_json(self, content, **kwargs):
        if content.get("command") == "connect":
            if self.room_id:
                await self.channel_layer.group_discard(
                    str(self.room_id), self.channel_name
                )
            self.room_id = content.get("room")
            asyncio.create_task(self.initialize_room())
        if content.get("command") == "send_message":
            asyncio.create_task(self.send_message(content))
        if content.get("command") == "fetch_prev_messages":
            asyncio.create_task(self.fetch_prev_messages(content))
        if content.get("command") == "update_display_name":
            asyncio.create_task(self.update_display_name(content))
        if content.get("command") == "update_member_limit":
            asyncio.create_task(self.update_member_limit(content))

    async def room_search_results(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def display_name_taken(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def member_limit(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def is_room_full(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def user_left_room(self, event):
        # Send message to WebSocket
        username = event["user_left_room"]
        if username == self.user.username:
            await self.channel_layer.group_discard(str(self.room_id), self.channel_name)
            await self.send_json(event)

    async def is_room_creator(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def members(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def new_message(self, event):
        if not self.oldest_message_timestamp:
            self.oldest_message_timestamp = datetime.datetime.fromtimestamp(
                event["new_message"]["created_at"]
            )
        await self.send_json(event)
        await self.read_conversation()

    async def messages(self, event):
        messages = event["messages"]
        if messages:
            oldest_message_timestamp = datetime.datetime.fromtimestamp(
                messages[0]["created_at"]
            )
            if not self.oldest_message_timestamp:
                self.oldest_message_timestamp = oldest_message_timestamp
            elif oldest_message_timestamp < self.oldest_message_timestamp:
                self.oldest_message_timestamp = oldest_message_timestamp
        await self.send_json(event)

    async def refreshed_messages(self, event):
        room = await database_sync_to_async(get_room)(self.room_id)
        messages = await database_sync_to_async(get_refreshed_messages)(
            room, self.oldest_message_timestamp
        )
        event["refreshed_messages"] = messages
        await self.send_json(event)
