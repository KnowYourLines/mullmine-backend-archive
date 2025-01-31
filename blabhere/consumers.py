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
    change_user_display_name,
    get_all_members,
    get_user,
    initialize_room,
    get_refreshed_messages,
    get_user_conversations,
    get_all_member_usernames,
    read_unread_conversation,
    leave_room,
    check_room_full,
    get_user_agreed_terms,
    agree_terms,
    block_room_user,
    report_room_user,
    delete_user,
    set_offline,
    set_online,
    get_all_room_ids,
    get_display_name,
    is_blocked_creator,
    find_rooms,
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

    async def fetch_agreed_terms_and_privacy(self):
        agreed_terms = await database_sync_to_async(get_user_agreed_terms)(
            self.user.username
        )
        await self.channel_layer.send(
            self.channel_name,
            {"type": "agreed_terms", "agreed_terms": agreed_terms},
        )

    async def exit_room(self, input_payload):
        usernames = await database_sync_to_async(get_all_member_usernames)(
            input_payload["room_id"]
        )
        await database_sync_to_async(leave_room)(self.user, input_payload["room_id"])
        for username in usernames:
            await self.channel_layer.group_send(
                username,
                {"type": "refresh_conversations"},
            )
        await self.channel_layer.group_send(
            input_payload["room_id"],
            {"type": "refresh_members"},
        )

    async def refresh_all_member_rooms(self):
        your_room_ids = await database_sync_to_async(get_all_room_ids)(self.user)
        for room_id in your_room_ids:
            await self.channel_layer.group_send(
                str(room_id),
                {"type": "refresh_members"},
            )

    async def go_online(self):
        await database_sync_to_async(set_online)(self.username)
        await self.refresh_all_member_rooms()

    async def connect(self):
        self.username = str(self.scope["url_route"]["kwargs"]["user_id"])
        self.user = self.scope["user"]
        if self.username == self.user.username:
            await self.channel_layer.group_add(self.username, self.channel_name)
            await self.accept()
            await self.go_online()
            await self.fetch_conversations()
            await self.fetch_display_name()
            await self.fetch_agreed_terms_and_privacy()
        else:
            await self.close()

    async def disconnect(self, close_code):
        await self.go_offline()
        await self.channel_layer.group_discard(self.username, self.channel_name)

    async def go_offline(self):
        await database_sync_to_async(set_offline)(self.username)
        await self.refresh_all_member_rooms()
        await self.channel_layer.group_send(
            self.username,
            {
                "type": "remain_online",
            },
        )

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
                    members = await database_sync_to_async(get_all_members)(room_id)
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

    async def agree_terms(self):
        await database_sync_to_async(agree_terms)(self.user.username)
        await self.fetch_agreed_terms_and_privacy()

    async def delete_account(self):
        await database_sync_to_async(delete_user)(self.user)

    async def receive_json(self, content, **kwargs):
        if self.username == self.user.username:
            if content.get("command") == "exit_room":
                asyncio.create_task(self.exit_room(content))
            if content.get("command") == "update_display_name":
                asyncio.create_task(self.update_display_name(content))
            if content.get("command") == "agree_terms":
                asyncio.create_task(self.agree_terms())
            if content.get("command") == "delete_account":
                asyncio.create_task(self.delete_account())

    async def display_name_taken(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def conversations(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def agreed_terms(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def remain_online(self, event):
        await database_sync_to_async(set_online)(self.username)
        await self.refresh_all_member_rooms()

    async def refresh_conversations(self, event):
        # Send message to WebSocket
        await self.fetch_conversations()


class RoomConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_id = None
        self.user = None
        self.oldest_message_timestamp = None

    async def connect(self):
        await self.accept()
        self.user = self.scope["user"]

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(str(self.room_id), self.channel_name)

    async def add_user_to_room(self, room):
        members, was_added = await database_sync_to_async(add_user_to_room)(
            self.user, room
        )
        if was_added:
            await self.channel_layer.group_send(
                self.room_id, {"type": "members", "members": members}
            )
            await self.channel_layer.group_send(
                self.user.username,
                {"type": "refresh_conversations"},
            )
        else:
            await self.channel_layer.send(
                self.channel_name, {"type": "members", "members": members}
            )

    async def fetch_initial_messages(self, room):
        messages = await database_sync_to_async(get_initial_messages)(room, self.user)
        await self.channel_layer.send(
            self.channel_name, {"type": "messages", "messages": messages}
        )

    async def fetch_display_name(self, room):
        display_name = await database_sync_to_async(get_display_name)(room)
        await self.channel_layer.send(
            self.channel_name, {"type": "display_name", "display_name": display_name}
        )

    async def fetch_prev_messages(self, input_payload):
        oldest_msg_id = input_payload.get("oldest_message_id", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        messages = await database_sync_to_async(get_prev_messages)(
            oldest_msg_id, room, self.user
        )
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

    async def initialize_room(self, input_payload):
        room_id = input_payload.get("room")
        is_room_full = await database_sync_to_async(check_room_full)(room_id, self.user)
        if not is_room_full:
            room = await database_sync_to_async(initialize_room)(room_id, self.user)
            if room:
                self.room_id = str(room.id)
                await self.channel_layer.group_add(self.room_id, self.channel_name)
                await self.add_user_to_room(room)
                await self.fetch_initial_messages(room)
                await self.fetch_display_name(room)
                await self.read_conversation()
                await self.channel_layer.send(
                    self.channel_name, {"type": "room", "room": str(room.id)}
                )
                usernames = await database_sync_to_async(get_all_member_usernames)(
                    self.room_id
                )
                for username in usernames:
                    await self.channel_layer.group_send(
                        username,
                        {"type": "refresh_conversations"},
                    )

    async def send_message(self, input_payload):
        message = input_payload.get("message", "")
        room = await database_sync_to_async(get_room)(self.room_id)
        creator = await database_sync_to_async(get_user)(self.user.username)
        if len(message.strip()) > 0 and creator.is_verified:
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

    async def block_user(self, input_payload):
        username = input_payload.get("username")
        if username and username != self.user.username:
            await database_sync_to_async(block_room_user)(
                self.room_id, self.user, username
            )

    async def report_user(self, input_payload):
        username = input_payload.get("username")
        if username and username != self.user.username:
            await database_sync_to_async(report_room_user)(
                self.room_id, self.user, username
            )

    async def search_rooms(self, input_payload):
        topic = input_payload.get("topic")
        rooms = await database_sync_to_async(find_rooms)(self.user, topic)
        await self.channel_layer.send(
            self.channel_name,
            {"type": "search_results", "search_results": rooms, "search_topic": topic},
        )

    async def receive_json(self, content, **kwargs):
        if content.get("command") == "connect":
            if self.room_id:
                await self.channel_layer.group_discard(
                    str(self.room_id), self.channel_name
                )
            asyncio.create_task(self.initialize_room(content))
        if content.get("command") == "find_rooms":
            asyncio.create_task(self.search_rooms(content))
        if content.get("command") == "send_message":
            asyncio.create_task(self.send_message(content))
        if content.get("command") == "fetch_prev_messages":
            asyncio.create_task(self.fetch_prev_messages(content))
        if content.get("command") == "block_user":
            asyncio.create_task(self.block_user(content))
        if content.get("command") == "report_user":
            asyncio.create_task(self.report_user(content))

    async def room(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def refresh_members(self, event):
        members = await database_sync_to_async(get_all_members)(self.room_id)
        await self.channel_layer.group_send(
            self.room_id, {"type": "members", "members": members}
        )

    async def members(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def search_results(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def display_name(self, event):
        # Send message to WebSocket
        await self.send_json(event)

    async def new_message(self, event):
        if not self.oldest_message_timestamp:
            self.oldest_message_timestamp = datetime.datetime.fromtimestamp(
                event["new_message"]["created_at"]
            )
        is_blocked = await database_sync_to_async(is_blocked_creator)(
            self.user, event["new_message"]["creator_username"]
        )
        if not is_blocked:
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
            room, self.oldest_message_timestamp, self.user
        )
        event["refreshed_messages"] = messages
        await self.send_json(event)
