import logging

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import F, Count, Case, When, Q

from blabhere.models import Room, Message, User, Conversation


def leave_room(user, room_id):
    room_to_leave = Room.objects.get(id=room_id)
    room_to_leave.members.remove(user)
    user.room_set.remove(room_to_leave)
    user.conversation_set.filter(room=room_to_leave).delete()
    if not room_to_leave.members.all():
        room_to_leave.delete()


def read_unread_conversation(room_id, user):
    room = Room.objects.get(id=room_id)
    conversation = Conversation.objects.get(participant=user, room=room)
    if not conversation.read:
        Conversation.objects.filter(id=conversation.id).update(read=True)


def get_user_conversations(username):
    user = User.objects.get(username=username)
    conversations = list(
        user.conversation_set.annotate(
            other_members=ArrayAgg(
                "room__members__display_name",
                filter=~Q(room__members__display_name=user.display_name),
                distinct=True,
            )
        )
        .values(
            "room__id",
            "read",
            "latest_message__creator__display_name",
            "latest_message__content",
            "latest_message__created_at",
            "other_members",
            "created_at",
        )
        .order_by(
            "read", F("latest_message__created_at").desc(nulls_last=True), "-created_at"
        )
    )
    for conversation in conversations:
        conversation["room__id"] = str(conversation["room__id"])
        conversation["created_at"] = conversation["created_at"].timestamp()
        if conversation["latest_message__created_at"]:
            conversation["latest_message__created_at"] = conversation[
                "latest_message__created_at"
            ].timestamp()
    return conversations


def check_room_full(room_id, user):
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        is_member = room.members.filter(username=user.username).exists()
        return room.members.all().count() >= 2 and not is_member


def get_room(room_id):
    try:
        room = Room.objects.get(id=room_id)
        return room
    except ObjectDoesNotExist:
        logging.error(f"Room id {room_id} does not exist")


def find_waiting_room(user):
    num_members = Count("members", distinct=True)
    user_rooms_members = User.objects.filter(room__in=user.room_set.all()).distinct()
    waiting_rooms = (
        Room.objects.all().annotate(num_members=num_members).filter(num_members=1)
    )
    other_users_waiting_rooms = waiting_rooms.exclude(
        members__in=user_rooms_members
    ).order_by("-created_at")
    your_own_waiting_rooms = waiting_rooms.filter(members=user).order_by("-created_at")
    pks_of_rooms_to_delete = list(
        your_own_waiting_rooms[1:].values_list("pk", flat=True)
    )
    Room.objects.filter(pk__in=pks_of_rooms_to_delete).delete()
    if other_users_waiting_rooms.exists() and your_own_waiting_rooms.exists():
        your_waiting_room = your_own_waiting_rooms.first()
        other_user_waiting_room = other_users_waiting_rooms.first()
        your_waiting_room.delete()
        return other_user_waiting_room
    elif other_users_waiting_rooms.exists():
        return other_users_waiting_rooms.first()
    elif your_own_waiting_rooms.exists():
        return your_own_waiting_rooms.first()
    else:
        return Room.objects.create()


def initialize_room(room_id, user):
    if user.is_verified:
        if room_id:
            try:
                room = Room.objects.get(id=room_id)
                if room.members.all().count() == 2:
                    return room
            except ObjectDoesNotExist:
                return None
        room = find_waiting_room(user)
        return room


def get_user(username):
    user = User.objects.get(username=username)
    return user


def get_all_member_display_names(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("display_name")
    return [member["display_name"] for member in members]


def get_all_member_usernames(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("username")
    return [member["username"] for member in members]


def add_user_to_room(user, room):
    was_added = False
    latest_message = room.message_set.order_by("-created_at").first()
    if user not in room.members.all():
        room.members.add(user)
        Conversation.objects.create(
            participant=user, room=room, latest_message=latest_message, read=True
        )
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


def get_refreshed_messages(room, oldest_message_timestamp):
    if oldest_message_timestamp:
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(
                created_at__gte=oldest_message_timestamp
            ).order_by("-created_at")[::-1]
        ]
    else:
        messages = []
    return messages


def create_new_message(content, room, creator):

    new_message = Message.objects.create(creator=creator, room=room, content=content)
    update_conversations_for_new_message(room, new_message)
    return {
        "creator_username": new_message.creator.username,
        "creator_display_name": new_message.creator.display_name,
        "content": new_message.content,
        "created_at": new_message.created_at.timestamp(),
        "id": str(new_message.id),
    }


def update_conversations_for_new_message(room, message):
    for user in room.members.all():
        conversation = Conversation.objects.get(participant=user, room=room)
        Conversation.objects.filter(id=conversation.id).update(
            latest_message=message, read=user == message.creator
        )


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


def change_user_display_name(user, new_name):
    rooms_to_refresh = [str(room["id"]) for room in user.room_set.all().values()]
    users_to_refresh = [
        str(conversation["participant__username"])
        for conversation in Conversation.objects.filter(
            latest_message__creator=user
        ).values("participant__username")
    ]
    try:
        User.objects.filter(id=user.id).update(display_name=new_name)
        return True, new_name, rooms_to_refresh, users_to_refresh
    except IntegrityError:
        return False, new_name, rooms_to_refresh, users_to_refresh
