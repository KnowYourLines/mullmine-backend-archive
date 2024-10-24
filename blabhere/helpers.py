import logging

from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator, EmptyPage
from django.db import IntegrityError
from django.db.models import (
    F,
    Count,
    OuterRef,
    Case,
    When,
    BooleanField,
    Q,
    Avg,
    DurationField,
    ExpressionWrapper,
    FloatField,
)
from django.db.models.functions import Now, ExtractDay, Cast
from django.db.models.lookups import GreaterThanOrEqual

from blabhere.models import Room, Message, User, Conversation


def room_search(page, user, size_query, name_query):
    try:
        count_user_msgs = Count("message", filter=Q(message__creator=user))
        user_rooms_msgs_count = user.room_set.annotate(count_user_msgs=count_user_msgs)
        avg_user_msgs_per_room = (
            user_rooms_msgs_count.aggregate(average=Avg("count_user_msgs"))["average"]
            or 0
        )
        user_active_rooms = user_rooms_msgs_count.filter(
            count_user_msgs__gte=avg_user_msgs_per_room
        )

        days_since_created = ExtractDay(
            ExpressionWrapper(Now() - F("created_at"), output_field=DurationField())
        )
        room_msg_senders = User.objects.filter(
            message__room__id=OuterRef("id")
        ).distinct()
        count_msg_senders = Count("members", filter=Q(members__in=room_msg_senders))
        user_rooms_members = (
            User.objects.filter(room__in=user.room_set.all())
            .exclude(id=user.id)
            .distinct()
        )
        user_active_rooms_members = (
            User.objects.filter(room__in=user_active_rooms)
            .exclude(id=user.id)
            .distinct()
        )
        count_user_rooms_members = Count(
            "members", filter=Q(members__in=user_rooms_members)
        )
        count_user_active_rooms_members = Count(
            "members", filter=Q(members__in=user_active_rooms_members)
        )
        count_user_rooms_members_msgs = Count(
            "message", filter=Q(message__creator__in=user_rooms_members)
        )
        count_user_active_rooms_members_msgs = Count(
            "message",
            filter=Q(message__creator__in=user_active_rooms_members),
        )
        senders_to_members = Case(
            When(
                num_members__gt=0,
                then=Cast(F("count_msg_senders"), FloatField())
                / Cast(F("num_members"), FloatField()),
            ),
            default=F("count_msg_senders"),
            output_field=FloatField(),
        )
        daily_msg_rate = Case(
            When(
                days_since_created__gt=0,
                then=Cast(F("count_room_msgs"), FloatField())
                / Cast(F("days_since_created"), FloatField()),
            ),
            default=F("count_room_msgs"),
            output_field=FloatField(),
        )
        room_full = Case(
            When(GreaterThanOrEqual(F("num_members"), F("max_num_members")), then=True),
            default=False,
            output_field=BooleanField(),
        )

        latest_message_timestamp = (
            Message.objects.filter(room__id=OuterRef("id"))
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        room_queryset = (
            Room.objects.all()
            .annotate(
                count_user_active_rooms_members_msgs=count_user_active_rooms_members_msgs
            )
            .annotate(count_user_active_rooms_members=count_user_active_rooms_members)
            .annotate(count_user_rooms_members_msgs=count_user_rooms_members_msgs)
            .annotate(count_user_rooms_members=count_user_rooms_members)
            .annotate(count_msg_senders=count_msg_senders)
            .annotate(count_room_msgs=Count("message", distinct=True))
            .annotate(days_since_created=days_since_created)
            .annotate(num_members=Count("members", distinct=True))
            .annotate(senders_to_members=senders_to_members)
            .annotate(daily_msg_rate=daily_msg_rate)
            .annotate(room_full=room_full)
            .annotate(latest_message_timestamp=latest_message_timestamp)
            .exclude(id__in=user.room_set.values("id"))
            .exclude(room_full=True)
        )
        if size_query:
            room_queryset = room_queryset.filter(num_members__lte=size_query)
        if name_query:
            room_queryset = room_queryset.filter(display_name__contains=name_query)
        room_queryset = room_queryset.order_by(
            "-count_user_active_rooms_members_msgs",
            "-count_user_active_rooms_members",
            "-count_user_rooms_members_msgs",
            "-count_user_rooms_members",
            "-senders_to_members",
            "-daily_msg_rate",
            "-count_room_msgs",
            "-num_members",
            F("latest_message_timestamp").desc(nulls_last=True),
            "-created_at",
        ).values(
            "display_name",
            "created_at",
            "id",
            "num_members",
            "max_num_members",
            "latest_message_timestamp",
        )
        rooms = Paginator(
            room_queryset,
            10,
        )
        rooms = rooms.page(page)
        for room in rooms:
            if room["latest_message_timestamp"]:
                room["latest_message_timestamp"] = room[
                    "latest_message_timestamp"
                ].timestamp()
            room["id"] = str(room["id"])
            room["created_at"] = room["created_at"].timestamp()
        return rooms.object_list, page
    except EmptyPage:
        return [], page


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
        conversation.read = True
        conversation.save()


def get_user_conversations(username):
    user = User.objects.get(username=username)
    conversations = list(
        user.conversation_set.values(
            "room__id",
            "room__display_name",
            "read",
            "latest_message__creator__display_name",
            "latest_message__content",
            "latest_message__created_at",
            "created_at",
        ).order_by(
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


def get_num_room_members(room):
    return room.members.all().count()


def is_room_creator(room, user):
    return room.creator and room.creator.username == user.username


def update_member_limit(new_limit, room, user):
    if room.creator and room.creator.username == user.username:
        room.max_num_members = new_limit
        room.save()
    return room


def check_room_full(room_id, user):
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        is_member = room.members.filter(username=user.username).exists()
        return (
            room.max_num_members
            and not (room.members.all().count() < room.max_num_members)
            and not is_member
        )


def get_room(room_id):
    try:
        room = Room.objects.get(id=room_id)
        return room
    except ObjectDoesNotExist:
        logging.error(f"Room id {room_id} does not exist")


def initialize_room(room_id, user):
    room, created = Room.objects.get_or_create(
        id=room_id,
        defaults={"display_name": room_id, "creator": user},
    )
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


def update_room_name(name, room, user):
    if room.creator and room.creator.username == user.username:
        try:
            room.display_name = name
            room.save()
            return True
        except IntegrityError:
            return False
    return False


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
        conversation.latest_message = message
        conversation.read = user == message.creator
        conversation.save()


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
        user.display_name = new_name
        user.save()
        return True, new_name, rooms_to_refresh, users_to_refresh
    except IntegrityError:
        return False, new_name, rooms_to_refresh, users_to_refresh
